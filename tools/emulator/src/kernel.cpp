// GPL-3.0. md-kernel: run one custom machine's DSP2 code outside the firmware.
//
// Loads a program at its bank, then for each input block optionally calls init
// and/or update, writes the packet words and 32 neighbour samples, calls render
// with the dispatcher's register contract, and writes the 32 output samples plus
// the whole 64-word state block (+0x00..+0x3F) and X:0x00..0x1F scratch.
//
// Registers and memory are poisoned before every call, and after the run every
// word outside the machine's declared regions is checked: output bank, state
// block, X scratch, optional pool slice, the program itself.
//
// Input record per block (little-endian u32):
//   flags (bit0 init, bit1 update/trig), packet[1..P], neighbour[0..31]
// Output record per block (u32, low 24 bits valid):
//   output[0..31], state[0x00..0x3F], xscratch[0x00..0x1F]
#include "dsp56kEmu/dsp.h"
#include "dsp56kEmu/jit.h"
#include "dsp56kEmu/peripherals.h"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using namespace dsp56k;

namespace {
constexpr TWord kCallSite = 0x100;           // scratch P location for the JSR stub
constexpr TWord kReturnPC = kCallSite + 2;
constexpr TWord kCurrentBank = 0x120, kPreviousBank = 0x100;
constexpr uint32_t kStateFill = 0x345678;

uint32_t poison(uint32_t a) { return (a * 1664525u + 1013904223u) & 0xffffff; }

[[noreturn]] void usage() {
    std::cerr <<
        "usage: md-kernel --program FILE --bank ADDR --init A --update A --render A\n"
        "                 --input FILE --output FILE [--packet N] [--track T]\n"
        "                 [--engine jit|interpreter] [--pool BASE:WORDS]\n"
        "                 [--load SPACE:ADDR:FILE]... [--max-steps N]\n"
        "Program and --load files are 3-byte little-endian DSP words.\n";
    std::exit(2);
}

uint32_t num(const std::string& s) { return static_cast<uint32_t>(std::stoul(s, nullptr, 0)); }

std::vector<uint32_t> words(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot read " + path);
    std::vector<uint32_t> w;
    unsigned char b[3];
    while (f.read(reinterpret_cast<char*>(b), 3)) w.push_back(b[0] | b[1] << 8 | b[2] << 16);
    return w;
}
}  // namespace

int main(int argc, char** argv) {
    std::map<std::string, std::string> opt;
    std::vector<std::string> loads;
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        if (k.rfind("--", 0) != 0 || i + 1 >= argc) usage();
        if (k == "--load") loads.push_back(argv[++i]);
        else opt[k.substr(2)] = argv[++i];
    }
    for (auto req : {"program", "bank", "init", "update", "render", "input", "output"})
        if (!opt.count(req)) usage();
    try {
        const bool interpreter = opt.count("engine") && opt["engine"] == "interpreter";
        const unsigned packet = opt.count("packet") ? num(opt["packet"]) : 8;
        const unsigned track = opt.count("track") ? num(opt["track"]) : 1;
        const uint64_t maxSteps = opt.count("max-steps") ? std::stoull(opt["max-steps"]) : 200000;
        if (packet > 0x3f || track > 15) throw std::runtime_error("bad --packet or --track");
        const TWord S = 0x800 + 0x40 * track;
        TWord poolBase = 0, poolWords = 0;
        if (opt.count("pool")) {
            auto p = opt["pool"]; auto c = p.find(':');
            poolBase = num(p.substr(0, c)); poolWords = num(p.substr(c + 1));
        }

        DefaultMemoryValidator validator;
        Memory memory(validator, 0x200000, 0x200000, 0x20000);   // external P/X/Y alias from 0x20000
        Peripherals56362 px; Peripherals56367 py;
        DSP dsp(memory, &px, &py);
        auto cfg = dsp.getJit().getConfig();
        cfg.maxInstructionsPerBlock = 1; cfg.enableOptimizer = false; cfg.linkJitBlocks = false;
        dsp.getJit().setConfig(cfg);

        // Poison everything a machine could wrongly depend on.
        for (TWord a = 0; a < 0x800; ++a) memory.set(MemArea_X, a, poison(a));
        for (TWord a = 0x100; a < 0x800; ++a) memory.set(MemArea_Y, a, poison(a ^ 0x5a5a5a));
        for (TWord a = 0x800; a < 0xc00; ++a) memory.set(MemArea_Y, a, kStateFill);
        for (TWord a = 0; a < poolWords; ++a) memory.set(MemArea_Y, poolBase + a, poison(poolBase + a));

        const TWord bank = num(opt["bank"]);
        const auto program = words(opt["program"]);
        for (size_t i = 0; i < program.size(); ++i) dsp.memWriteP(bank + static_cast<TWord>(i), program[i]);
        struct Load { EMemArea area; TWord addr; std::vector<uint32_t> w; };
        std::vector<Load> loaded;
        for (auto& l : loads) {
            auto c1 = l.find(':'), c2 = l.find(':', c1 + 1);
            const char sp = l[0];
            const EMemArea area = sp == 'P' ? MemArea_P : sp == 'X' ? MemArea_X : MemArea_Y;
            Load ld{area, num(l.substr(c1 + 1, c2 - c1 - 1)), words(l.substr(c2 + 1))};
            for (size_t i = 0; i < ld.w.size(); ++i) memory.set(area, ld.addr + static_cast<TWord>(i), ld.w[i]);
            loaded.push_back(std::move(ld));
        }
        memory.set(MemArea_Y, 0x140, kCurrentBank);
        memory.set(MemArea_Y, 0x141, S);
        memory.set(MemArea_Y, 0x142, track);

        uint64_t seed = 1;
        auto rnd = [&]() { seed = seed * 6364136223846793005ull + 1442695040888963407ull; return uint32_t(seed >> 40); };
        uint64_t worstCycles = 0, worstInstr = 0, totalCycles = 0, renders = 0;

        auto call = [&](TWord entry, bool render) {
            dsp.memWriteP(kCallSite, 0x0bf080);          // jsr >entry
            dsp.memWriteP(kCallSite + 1, entry);
            for (auto r : {Reg_X0, Reg_X1, Reg_Y0, Reg_Y1, Reg_A0, Reg_A1, Reg_B0, Reg_B1})
                dsp.writeReg(r, TReg24(int32_t(rnd())));
            for (int i = 0; i < 8; ++i) {
                dsp.writeReg(static_cast<EReg>(Reg_N0 + i), TReg24(int32_t(rnd())));
                if (i < 6) dsp.writeReg(static_cast<EReg>(Reg_R0 + i), TReg24(int32_t(rnd())));
                dsp.writeReg(static_cast<EReg>(Reg_M0 + i), TReg24(render && i == 7 ? 0x1f : 0xffffff));
            }
            dsp.writeReg(Reg_R6, TReg24(int32_t(S)));
            dsp.writeReg(Reg_R7, TReg24(int32_t(render ? kCurrentBank : rnd())));
            dsp.regs().sr.var = 0x800d0;
            const auto c0 = dsp.getCycles(), i0 = dsp.getInstructionCounter();
            dsp.setPC(kCallSite);
            uint64_t steps = 0;
            while (dsp.getPC().toWord() != kReturnPC) {
                if (++steps > maxSteps) throw std::runtime_error("call did not return (step limit)");
                if (interpreter) dsp.execInterpreter(); else dsp.execJit();
            }
            if (render) {
                const auto c = dsp.getCycles() - c0;
                worstCycles = std::max<uint64_t>(worstCycles, c);
                worstInstr = std::max<uint64_t>(worstInstr, dsp.getInstructionCounter() - i0);
                totalCycles += c; ++renders;
            }
        };

        std::ifstream in(opt["input"], std::ios::binary);
        std::ofstream out(opt["output"], std::ios::binary);
        if (!in || !out) throw std::runtime_error("cannot open input/output");
        std::vector<uint32_t> rec(1 + packet + 32);
        const TWord init = num(opt["init"]), update = num(opt["update"]), render = num(opt["render"]);
        while (in.read(reinterpret_cast<char*>(rec.data()), rec.size() * 4)) {
            if (rec[0] & 1) call(init, false);
            for (unsigned i = 0; i < packet; ++i) memory.set(MemArea_Y, S + 1 + i, rec[1 + i] & 0xffffff);
            for (unsigned i = 0; i < 32; ++i) memory.set(MemArea_Y, kPreviousBank + i, rec[1 + packet + i] & 0xffffff);
            if (rec[0] & 2) {
                memory.set(MemArea_Y, S, 0);                 // the dispatcher clears the pending word first
                call(update, false);
            }
            for (unsigned i = 0; i < 32; ++i) memory.set(MemArea_Y, kCurrentBank + i, poison(i + 0x777));
            call(render, true);
            std::vector<uint32_t> o;
            for (unsigned i = 0; i < 32; ++i) o.push_back(memory.get(MemArea_Y, kCurrentBank + i));
            for (unsigned i = 0; i < 0x40; ++i) o.push_back(memory.get(MemArea_Y, S + i));
            for (unsigned i = 0; i < 0x20; ++i) o.push_back(memory.get(MemArea_X, i));
            out.write(reinterpret_cast<const char*>(o.data()), o.size() * 4);
        }

        // Guards: nothing outside the machine's regions may change.
        auto fail = [](const std::string& what, EMemArea a, TWord addr) {
            std::cerr << "guard: " << what << " changed at " << "PXY"[a] << ":0x" << std::hex << addr << "\n";
            std::exit(1);
        };
        for (TWord a = 0x20; a < 0x800; ++a) if (memory.get(MemArea_X, a) != poison(a)) fail("X memory", MemArea_X, a);
        for (TWord a = 0x143; a < 0x800; ++a) if (memory.get(MemArea_Y, a) != poison(a ^ 0x5a5a5a)) fail("Y memory", MemArea_Y, a);
        for (TWord a = 0x800; a < 0xc00; ++a)
            if ((a < S || a >= S + 0x40) && memory.get(MemArea_Y, a) != kStateFill) fail("another track's state", MemArea_Y, a);
        if (memory.get(MemArea_Y, 0x140) != kCurrentBank || memory.get(MemArea_Y, 0x142) != track) fail("scheduler words", MemArea_Y, 0x140);
        for (size_t i = 0; i < program.size(); ++i)
            if (memory.get(MemArea_P, bank + static_cast<TWord>(i)) != program[i]) fail("program", MemArea_P, bank + TWord(i));
        for (auto& ld : loaded)
            for (size_t i = 0; i < ld.w.size(); ++i)
                if (memory.get(ld.area, ld.addr + TWord(i)) != ld.w[i]) fail("loaded table", ld.area, ld.addr + TWord(i));
        std::cout << "blocks=" << renders << " worst_block_cycles=" << worstCycles
                  << " mean_block_cycles=" << (renders ? totalCycles / renders : 0)
                  << " worst_block_instructions=" << worstInstr << " guards=pass\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
