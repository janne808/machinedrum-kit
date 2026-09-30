// Regression tests for published DSP register state and monitored JIT execution.
#include "dsp56kEmu/assembler.h"
#include "dsp56kEmu/dsp.h"
#include "dsp56kEmu/jit.h"
#include "dsp56kEmu/peripherals.h"
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace dsp56k;
void check(bool b, const char *message) {
    if (!b)
        throw std::runtime_error(message);
}
int main() {
    try {
        for (unsigned blockSize : {1u, 32u}) {
            DefaultMemoryValidator validator;
            Memory memory(validator, 0x1000, 0x10000, 0x8000);
            Peripherals56362 px;
            Peripherals56367 py;
            DSP dsp(memory, &px, &py);
            auto config = dsp.getJit().getConfig();
            config.maxInstructionsPerBlock = blockSize;
            config.enableOptimizer = false;
            config.linkJitBlocks = false;
            config.memoryWritesCallCpp = true;
            dsp.getJit().setConfig(config);
            Assembler assembler;
            auto emit = [&](TWord pc, const char *text) {
                auto a = assembler.assemble(text);
                check(a.success(), text);
                for (unsigned i = 0; i < a.wordCount; ++i)
                    dsp.memWriteP(pc + i, a.word[i]);
                return pc + a.wordCount;
            };
            auto run = [&](TWord end) {
                for (unsigned i = 0; i < 1000 && dsp.getPC().toWord() != end; ++i)
                    dsp.execJit();
                check(dsp.getPC().toWord() == end, "DSP failed to return from monitored program");
            };
            for (const char *repeat : {"rep #$4", "rep x0"}) {
                dsp.resetHW();
                dsp.regs().x.var = 4;
                dsp.regs().a.var = 0;
                dsp.regs().b.var = uint64_t(0x1000000) << 8;
                auto end = emit(0x100, "jsr $200");
                emit(end, "nop");
                auto body = emit(0x200, repeat);
                auto after = emit(body, "add b,a");
                emit(after, "rts");
                std::vector<uint64_t> values;
                dsp.debugInstruction = [&](TWord pc) {
                    if (pc == body)
                        values.push_back((uint64_t(dsp.regs().a.var) >> 8));
                };
                dsp.setPC(0x100);
                run(end);
                check((uint64_t(dsp.regs().a.var) >> 8) == 0x4000000, "REP result corrupted by hooks");
                check(values == std::vector<uint64_t>({0, 0x1000000, 0x2000000, 0x3000000}),
                      "REP instruction hook snapshots are incomplete or stale");
            }
            dsp.resetHW();
            dsp.regs().a.var = 0;
            dsp.regs().b.var = uint64_t(0x1000000) << 8;
            auto end = emit(0x100, "jsr $200");
            emit(end, "nop");
            emit(0x200, "do #$5,>$204");
            emit(0x202, "add b,a");
            emit(0x203, "nop");
            emit(0x204, "rts");
            std::vector<uint64_t> values;
            dsp.debugInstruction = [&](TWord pc) {
                if (pc == 0x202)
                    values.push_back((uint64_t(dsp.regs().a.var) >> 8));
            };
            dsp.setPC(0x100);
            run(end);
            check((uint64_t(dsp.regs().a.var) >> 8) == 0x5000000 && values.size() == 5,
                  "DO loop corrupted by hooks");
            for (unsigned i = 0; i < values.size(); ++i)
                check(values[i] == uint64_t(i) * 0x1000000, "DO snapshot stale");
            // Memory reads must be observed independently of instruction fetches and inspector reads.
            dsp.resetHW();
            struct Access {
                bool write;
                EMemArea space;
                TWord addr, value;
            };
            std::vector<Access> accesses;
            dsp.debugAccess = [&](bool w, EMemArea s, TWord a, TWord v) { accesses.push_back({w, s, a, v}); };
            dsp.debugInstruction = [](TWord) {};
            TWord pc = 0x300;
            pc = emit(pc, "move #$123456,x0");
            pc = emit(pc, "move x0,x:>$40");
            pc = emit(pc, "move x:>$40,y0");
            pc = emit(pc, "move #$40,r0");
            pc = emit(pc, "move x:(r0)+,y1");
            emit(pc, (std::string("jmp $") +
                      [](TWord v) {
                          std::ostringstream s;
                          s << std::hex << v;
                          return s.str();
                      }(pc))
                         .c_str());
            accesses.clear();
            dsp.setPC(0x300);
            run(pc);
            check(dsp.y0().toWord() == 0x123456 && dsp.y1().toWord() == 0x123456,
                  "Monitored memory operations changed results");
            unsigned reads = 0, writes = 0;
            for (auto a : accesses)
                if (a.space == MemArea_X && a.addr == 0x40) {
                    if (a.write) {
                        ++writes;
                        check(a.value == 0x123456, "Wrong write value");
                    } else
                        ++reads;
                }
            check(reads == 2 && writes == 1, "Missing/duplicate immediate or dynamic memory hooks");
            auto count = accesses.size();
            check(memory.get(MemArea_X, 0x40) == 0x123456, "Bad inspector value");
            check(accesses.size() == count, "Inspector generated watchpoint accesses");
            // Publishing an X1/Y1 snapshot used to shift the still-live cached source
            // register. Repeated stores expose that corruption after the first iteration.
            dsp.resetHW();
            dsp.debugAccess = {};
            dsp.regs().x.var = uint64_t(0x123456) << 24;
            dsp.regs().y.var = uint64_t(0x654321) << 24;
            dsp.regs().r[0].var = 0x50;
            dsp.regs().r[1].var = 0x60;
            end = emit(0x100, "jsr $400");
            emit(end, "nop");
            pc = emit(0x400, "rep #$8");
            pc = emit(pc, "move x1,x:(r0)+");
            pc = emit(pc, "rep #$8");
            pc = emit(pc, "move y1,y:(r1)+");
            emit(pc, "rts");
            dsp.debugInstruction = [](TWord) {};
            dsp.setPC(0x100);
            run(end);
            check(dsp.x1().toWord() == 0x123456 && dsp.y1().toWord() == 0x654321,
                  "Snapshot destroyed cached XY high registers");
            for (unsigned i = 0; i < 8; ++i) {
                check(memory.get(MemArea_X, 0x50 + i) == 0x123456, "REP X1 stores corrupted by publication");
                check(memory.get(MemArea_Y, 0x60 + i) == 0x654321, "REP Y1 stores corrupted by publication");
            }
        }
        std::cout << "DSP debugger hooks: REP, DO, published accumulators, and memory observers passed\n";
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
