#include <set>
// GPL-3.0. Headless monitor: one emulation owner, stopped by a rendezvous.
#include "baseLib/filesystem.h"
#include "coldfire_disasm.h"
#include "dsp56kEmu/opcodeanalysis.h"
#include "mc68k/cpuState.h"
#include "mdLib/mdhardware.h"
#include "mdLib/mdromloader.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <vector>

namespace {
std::string quote(const std::string &s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\')
            o << '\\' << c;
        else if (c < 32)
            o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else
            o << c;
    }
    return o.str() + '"';
}
struct Object {
    std::vector<std::string> fields;
    void raw(const std::string &k, const std::string &v) { fields.push_back(quote(k) + ":" + v); }
    void text(const std::string &k, const std::string &v) { raw(k, quote(v)); }
    void num(const std::string &k, uint64_t v) { raw(k, std::to_string(v)); }
    void real(const std::string &k, double v) {
        std::ostringstream s;
        s << std::setprecision(12) << v;
        raw(k, s.str());
    }
    void boolean(const std::string &k, bool v) { raw(k, v ? "true" : "false"); }
    std::string str() const {
        std::string s = "{";
        for (const auto &f : fields) {
            if (s.size() > 1)
                s += ',';
            s += f;
        }
        return s + '}';
    }
};
std::string array(const std::vector<std::string> &v) {
    std::string s = "[";
    for (const auto &e : v) {
        if (s.size() > 1)
            s += ',';
        s += e;
    }
    return s + ']';
}
void require(bool condition, const std::string &message) {
    if (!condition)
        throw std::runtime_error(message);
}
uint64_t number(const std::string &s, uint64_t max = UINT32_MAX) {
    require(!s.empty() && s[0] != '-', "Expected unsigned integer");
    size_t end = 0;
    uint64_t v = std::stoull(s, &end, 0);
    require(end == s.size() && v <= max, "Integer outside permitted range: " + s);
    return v;
}
std::vector<std::string> tokenize(const std::string &line) {
    std::vector<std::string> tokens;
    std::istringstream input(line);
    std::string t;
    while (input >> std::ws && input.peek() != EOF) {
        input >> std::quoted(t);
        require(bool(input), "Unterminated quote");
        tokens.push_back(t);
    }
    return tokens;
}
const char *cpuName(int c) { return c == 0 ? "coldfire" : c == 1 ? "dsp1" : c == 2 ? "dsp2" : "machine"; }
int cpuNumber(const std::string &n) {
    if (n == "coldfire" || n == "cf")
        return 0;
    if (n == "dsp1")
        return 1;
    if (n == "dsp2")
        return 2;
    throw std::runtime_error("CPU must be coldfire, dsp1 or dsp2");
}
int spaceNumber(const std::string &n, int cpu) {
    if (cpu == 0) {
        require(n == "B", "ColdFire uses byte-addressed space B");
        return 0;
    }
    if (n == "P")
        return 0;
    if (n == "X")
        return 1;
    if (n == "Y")
        return 2;
    throw std::runtime_error("DSP space must be P, X or Y (word addresses)");
}
struct Event {
    uint64_t sequence = 0, cycles = 0;
    int cpu = 0, space = 0;
    const char *kind = "instruction";
    uint32_t pc = 0, address = 0, value = 0, width = 0;
    bool valueKnown = false;
    bool transfer = false;
    int destinationSpace = 0;
    uint32_t destination = 0, transferCount = 0;
    std::string json() const {
        Object o;
        o.num("sequence", sequence);
        o.text("cpu", cpuName(cpu));
        o.num("pc", pc);
        o.text("kind", kind);
        o.text("space", cpu == 0 ? "B" : std::string(1, "PXY"[space]));
        o.num("address", address);
        o.num("width", width);
        o.num("cycles", cycles);
        o.raw("value", valueKnown ? std::to_string(value) : "null");
        if (transfer) {
            o.text("destination_space", std::string(1, "PXY"[destinationSpace]));
            o.num("destination", destination);
            o.num("transfer_count", transferCount);
        }
        return o.str();
    }
};
struct Breakpoint {
    unsigned id;
    int cpu;
    uint32_t address;
};
struct Watchpoint {
    unsigned id;
    int cpu, space;
    uint32_t address, count;
    std::string mode;
};
struct Symbol {
    int cpu, space;
    uint32_t address;
    std::string name, provenance;
};
class Monitor;
Monitor *active = nullptr;
std::atomic<bool> interrupted{false};
void signalPause(int) { interrupted.store(true, std::memory_order_relaxed); }

class Monitor {
    std::unique_ptr<md::Hardware> hw;
    std::thread worker;
    std::mutex mutex;
    std::condition_variable cv;
    bool paused = false;
    std::atomic<bool> quitting{false};
    std::atomic<bool> pauseRequested{true};
    std::string reason = "reset", workerError;
    int selected = 0, stoppedCpu = 0, stepCpu = -1;
    uint64_t stepTarget = 0, frames = 0, deadline = 0;
    bool bootRequested = false;
    uint64_t captureRemaining = 0;
    bool audioPrimed = false;
    unsigned captureChannels = 2;
    double inputHz = 0, inputLevel = 0, inputPhase = 0;
    unsigned inputMask = 3;
    std::string captureFile;
    std::vector<float> capture;
    float capturePeak = 0;
    double captureEnergy = 0;
    std::array<uint64_t, 3> instructions{};
    std::array<uint32_t, 3> pcs{};
    std::array<bool, 3> instructionBoundary{};
    std::vector<Breakpoint> breaks;
    std::unordered_map<uint64_t, unsigned> breakIndex;
    std::vector<Watchpoint> watches;
    std::vector<Symbol> symbols;
    unsigned nextId = 1, hitId = 0;
    std::array<bool, 3> watchPending{};
    std::array<Event, 3> watchEvents{};
    std::array<unsigned, 3> watchIds{};
    Event hitEvent;
    enum class Trace { Off, Io, All, Program, Cpu };
    int traceCpu = 0;
    Trace trace = Trace::Off;
    static constexpr size_t capacity = 8192;
    std::array<Event, capacity> ring{};
    uint64_t eventSequence = 0, eventsWritten = 0, workaroundCalls = 0;

    struct AudioBudget {
        bool enabled = false;
        uint64_t dacWrites = 0, activeHalfWrites = 0, mixerBlocks = 0;
        uint64_t trackedDacReads = 0, staleDacReads = 0, untrackedDacReads = 0;
        std::array<uint64_t, 0x180> dacVersions{}, dacReadVersions{};
        uint64_t mixerPrevious = 0, mixerPeriodMax = 0;
        std::array<uint64_t, 16> calls{}, totalCycles{}, maxCycles{};
        uint64_t voiceStart = 0;
        unsigned voiceTrack = 16;
        std::vector<std::string> collisions;
    } audioBudget;
    // PC timestamp log: diagnostic timelines across both DSPs (each record keeps
    // both DSP cycle counters and DSP1 DMA0/DMA4 destination registers).
    struct PcRecord { uint32_t cpu, pc; uint64_t c1, c2; uint32_t ddr0, ddr4, r6; uint64_t c0; };
    std::set<uint64_t> pcLogSet;
    std::vector<PcRecord> pcLog;
    size_t pcLogCap = 4000000;
    std::array<uint64_t, 3> pcSample{}, pcSampleCount{};
    std::string budgetStatus() {
        Object o;
        o.boolean("enabled", audioBudget.enabled);
        o.num("dac_buffer_writes", audioBudget.dacWrites);
        o.num("writes_to_active_dac_half", audioBudget.activeHalfWrites);
        o.num("tracked_dac_reads", audioBudget.trackedDacReads);
        o.num("stale_dac_reads", audioBudget.staleDacReads);
        o.num("untracked_dac_reads", audioBudget.untrackedDacReads);
        o.num("mixer_blocks", audioBudget.mixerBlocks);
        o.num("mixer_period_max", audioBudget.mixerPeriodMax);
        o.num("nominal_block_cycles", 73728);
        std::vector<std::string> voices;
        for(unsigned t=0;t<16;++t) {
            Object v;v.num("track",t);v.num("calls",audioBudget.calls[t]);
            v.num("total_cycles",audioBudget.totalCycles[t]);v.num("max_cycles",audioBudget.maxCycles[t]);
            voices.push_back(v.str());
        }
        o.raw("voices",array(voices));o.raw("collision_examples",array(audioBudget.collisions));
        o.text("semantics", "OS 1.63: DSP2 B4/B5 render spans; DSP1 4F block periods; CPU writes to X:400..57F compared with DMA1 source half. Active-half writes indicate overlap. Stale DAC reads are repeat DMA reads without a CPU write since the previous read, for words written after budget reset; untracked words are counted separately. Mixer-start period is scheduling jitter, not by itself an underrun. JIT cycle accounting is approximate.");
        return o.str();
    }

    dsp56k::DSP &dsp(int cpu) { return cpu == 1 ? hw->getDspMixer().dsp() : hw->getDspProducer().dsp(); }
    uint64_t cycles(int cpu) { return cpu == 0 ? hw->getUC().getCycles() : dsp(cpu).getCycles(); }
    void record(Event e) {
        e.sequence = ++eventSequence;
        e.cycles = cycles(e.cpu);
        ring[eventsWritten++ % capacity] = e;
    }
    void stop(int cpu, const std::string &why) {
        std::unique_lock lock(mutex);
        if (quitting.load())
            return;
        stoppedCpu = cpu;
        reason = why;
        paused = true;
        pauseRequested = false;
        stepCpu = -1;
        bootRequested = false;
        cv.notify_all();
        cv.wait(lock, [&] { return !paused || quitting.load(); });
    }
    void instruction(int cpu, uint32_t pc) {
        struct Boundary {
            bool &active;
            Boundary(bool &b) : active(b) { active = true; }
            ~Boundary() { active = false; }
        } boundary(instructionBoundary[cpu]);
        pcs[cpu] = pc;
        ++instructions[cpu];
        if (quitting.load())
            return;
        if(audioBudget.enabled) {
            if(cpu==2 && pc==0xb4) {
                auto r6=dsp(2).regs().r[6].var;
                audioBudget.voiceTrack=r6>=0x800 && r6<0xc00 ? (r6-0x800)/64 : 16;
                audioBudget.voiceStart=cycles(2);
            } else if(cpu==2 && pc==0xb5 && audioBudget.voiceTrack<16) {
                auto t=audioBudget.voiceTrack;auto delta=cycles(2)-audioBudget.voiceStart;
                ++audioBudget.calls[t];audioBudget.totalCycles[t]+=delta;
                audioBudget.maxCycles[t]=std::max(audioBudget.maxCycles[t],delta);
                audioBudget.voiceTrack=16;
            } else if(cpu==1 && pc==0x4f) {
                auto now=cycles(1);
                if(audioBudget.mixerBlocks) audioBudget.mixerPeriodMax=std::max(audioBudget.mixerPeriodMax,now-audioBudget.mixerPrevious);
                ++audioBudget.mixerBlocks;audioBudget.mixerPrevious=now;
            }
        }
        const bool sampled = pcSample[cpu] && ++pcSampleCount[cpu] % pcSample[cpu] == 0;
        if (pcLog.size() < pcLogCap && (sampled || (!pcLogSet.empty() && pcLogSet.count((uint64_t(cpu) << 32) | pc)))) {
            auto &dma = hw->getDspMixer().getPeriph().getDMA();
            pcLog.push_back({uint32_t(cpu), pc, cycles(1), cycles(2), dma.getDDR(0), dma.getDDR(4),
                             cpu ? uint32_t(dsp(cpu).regs().r[6].var) : 0u, cycles(0)});
        }
        if (trace == Trace::All || (trace == Trace::Cpu && cpu == traceCpu))
            record({0, 0, cpu, 0, "instruction", pc, pc, 0, 0, false});
        if (watchPending[cpu]) {
            hitEvent = watchEvents[cpu];
            hitId = watchIds[cpu];
            watchPending[cpu] = false;
            stop(cpu, "watchpoint");
            return;
        }
        auto b = breakIndex.find((uint64_t(cpu) << 32) | pc);
        if (b != breakIndex.end()) {
            hitId = b->second;
            stop(cpu, "breakpoint");
            return;
        }
        if (stepCpu == cpu && instructions[cpu] >= stepTarget) {
            stop(cpu, "step");
            return;
        }
        if (pauseRequested.load(std::memory_order_relaxed) || interrupted.exchange(false))
            stop(cpu, reason == "reset" ? "reset" : "pause");
    }
    bool ioAddress(int cpu, int space, uint32_t a) const {
        if (cpu == 0)
            return (a >= 0x300000 && a < 0x310000) || (a >= 0x500000 && a < 0x500008) ||
                   (a >= 0x600000 && a < 0x600008);
        return space != 0 && a >= 0xffff80;
    }
    void access(int cpu, bool write, int space, uint32_t address, uint32_t value, unsigned width) {
        if (quitting.load())
            return;
        if(audioBudget.enabled && cpu==1 && write && space==1 && address>=0x400 && address<0x580) {
            ++audioBudget.dacWrites;
            ++audioBudget.dacVersions[address-0x400];
            const auto reader=hw->getDspMixer().getPeriph().getDMA().getDSR(1);
            if(reader>=0x400 && reader<0x580 && (reader-0x400)/0xc0==(address-0x400)/0xc0) {
                ++audioBudget.activeHalfWrites;
                if(audioBudget.collisions.size()<16) {
                    Object e;e.num("pc",pcs[1]);e.num("write_address",address);e.num("dma1_source",reader);e.num("cycles",cycles(1));
                    audioBudget.collisions.push_back(e.str());
                }
            }
        }
        const bool io = ioAddress(cpu, space, address);
        Event event{
            0,        0,       cpu,   space, write ? (io ? "io_write" : "write") : (io ? "io_read" : "read"),
            pcs[cpu], address, value, width, write};
        if (trace == Trace::All || (trace == Trace::Cpu && cpu == traceCpu) ||
            (trace == Trace::Io && io) ||
            (trace == Trace::Program && cpu == traceCpu && space == 0 && write))
            record(event);
        for (const auto &w : watches) {
            if (w.cpu != cpu || w.space != space || w.mode.find(write ? 'w' : 'r') == std::string::npos)
                continue;
            if (uint64_t(address) < uint64_t(w.address) + w.count &&
                uint64_t(w.address) < uint64_t(address) + width && !watchPending[cpu]) {
                event.cycles = cycles(cpu);
                watchPending[cpu] = true;
                watchEvents[cpu] = event;
                watchIds[cpu] = w.id;
            }
        }
    }
    void event(int cpu, const char *kind, uint32_t address, uint32_t value) {
        const std::string_view name(kind);
        // DMA reads expose actual consumption, independently of mixer-start jitter.
        // Only track words written since reset; untouched constant output slots
        // cannot establish a freshness contract from this observation window.
        if(audioBudget.enabled && cpu==1 && name=="dma_read" && value==1 && address>=0x400 && address<0x580) {
            const auto i=address-0x400;
            const auto version=audioBudget.dacVersions[i];
            if(version) {
                ++audioBudget.trackedDacReads;
                if(audioBudget.dacReadVersions[i]==version) ++audioBudget.staleDacReads;
                audioBudget.dacReadVersions[i]=version;
            } else ++audioBudget.untrackedDacReads;
        }
        if (name == "task_workaround")
            ++workaroundCalls;
        if (trace == Trace::Off || trace == Trace::Program ||
            (trace == Trace::Cpu && cpu != traceCpu))
            return;
        Event e{0, 0, cpu, 0, kind, pcs[cpu], address, value, 0, true};
        if (name == "dma_read") {
            e.space = static_cast<int>(value);
            e.valueKnown = false;
            e.width = 1;
        } else if (name == "dma_write_P" || name == "dma_write_X" || name == "dma_write_Y") {
            e.space = name.back() == 'X' ? 1 : name.back() == 'Y' ? 2 : 0;
            e.kind = "dma_write";
            e.width = 1;
        }
        record(e);
    }
    static void cpuHook(m68ki_cpu_core *, unsigned pc) {
        if (active)
            active->instruction(0, pc);
    }
    void writeAudio() {
        std::ofstream f(captureFile, std::ios::binary);
        require(bool(f), "Cannot create audio capture");
        auto le = [&](uint32_t v, unsigned n) {
            for (unsigned i = 0; i < n; ++i)
                f.put(char(v >> (i * 8)));
        };
        f.write("RIFF", 4);
        le(36 + capture.size() * 2, 4);
        f.write("WAVEfmt ", 8);
        le(16, 4);
        le(1, 2);
        le(captureChannels, 2);
        le(44100, 4);
        le(44100 * captureChannels * 2, 4);
        le(captureChannels * 2, 2);
        le(16, 2);
        f.write("data", 4);
        le(capture.size() * 2, 4);
        for (float v : capture)
            le(static_cast<uint16_t>(static_cast<int16_t>(std::clamp(v, -1.f, 1.f) * 32767)), 2);
        require(bool(f), "Audio capture write failed");
    }
    void runWorker() {
        try {
            std::array<std::array<float, 256>, 6> channels{};
            synthLib::TAudioOutputs outputs{};
            std::array<std::array<float, 256>, 2> inputChannels{};
            synthLib::TAudioInputs inputs{};
            for (unsigned c = 0; c < 2; ++c) inputs[c] = inputChannels[c].data();
            for (unsigned c = 0; c < 6; ++c)
                outputs[c] = channels[c].data();
            while (!quitting.load()) {
                auto batch = static_cast<unsigned>(
                    deadline > frames ? std::min<uint64_t>(256, deadline - frames) : 256);
                if (captureRemaining) {
                    if (!audioPrimed) {
                        hw->processAudio(outputs, 0, 1);
                        hw->processAudio(outputs, 0, 0);
                        audioPrimed = true;
                    }
                    batch = static_cast<unsigned>(std::min<uint64_t>(batch, captureRemaining));
                    if (inputHz > 0) {
                        constexpr double tau = 6.2831853071795864769;
                        for (unsigned i = 0; i < batch; ++i) {
                            const float v = float(std::sin(inputPhase) * inputLevel);
                            inputPhase = std::fmod(inputPhase + tau * inputHz / 44100.0, tau);
                            for (unsigned c = 0; c < 2; ++c)
                                inputChannels[c][i] = (inputMask & (1u << c)) ? v : 0.f;
                        }
                        hw->processAudio(inputs, outputs, batch, 0);
                    } else {
                        hw->processAudio(outputs, batch, 0);
                    }
                    frames += batch;
                    captureRemaining -= batch;
                    for (unsigned i = 0; i < batch; ++i) {
                        for (unsigned c = 0; c < 6; ++c)
                            require(std::isfinite(channels[c][i]), "Non-finite DSP audio");
                        for (unsigned c = 0; c < captureChannels; ++c) {
                            float v = channels[c][i];
                            capture.push_back(v);
                            capturePeak = std::max(capturePeak, std::abs(v));
                            captureEnergy += double(v) * v;
                        }
                    }
                    if (!captureRemaining) {
                        writeAudio();
                        stop(-1, "audio_complete");
                        continue;
                    }
                } else {
                    hw->advance(batch);
                    frames += batch;
                }
                if (bootRequested && hw->isFirmwareMidiReady() &&
                    hw->getFrontPanelSnapshot().countLitPixels() > 0)
                    stop(-1, "boot_ready");
                else if (deadline && frames >= deadline)
                    stop(-1, "frame_budget");
                else if (pauseRequested.load() || interrupted.exchange(false))
                    stop(-1, "pause");
            }
        } catch (const std::exception &e) {
            std::lock_guard lock(mutex);
            workerError = e.what();
            paused = true;
            reason = "error";
            cv.notify_all();
        }
    }
    void needPaused() {
        require(paused, "Machine is running; use pause or wait first");
        require(workerError.empty(), workerError);
    }
    uint32_t address(const std::string &text, int cpu, int space) {
        for (const auto &s : symbols)
            if (s.cpu == cpu && s.space == space && s.name == text)
                return s.address;
        return static_cast<uint32_t>(number(text, cpu == 0 ? UINT32_MAX : 0xffffff));
    }
    std::string label(int cpu, int space, uint32_t addr) {
        for (const auto &s : symbols)
            if (s.cpu == cpu && s.space == space && s.address == addr)
                return s.name;
        return "";
    }
    uint32_t peek(int cpu, int space, uint32_t addr) {
        if (cpu == 0) {
            uint8_t value;
            require(hw->getUC().debugPeek(addr, value),
                    "MMIO/unmapped memory cannot be peeked; use peripherals for non-destructive snapshots");
            return value;
        }
        require(addr < 0xffff80, "DSP MMIO cannot be peeked; use peripherals");
        auto &mem = dsp(cpu).memory();
        auto area = static_cast<dsp56k::EMemArea>(space);
        auto translated = area;
        if (mem.getBridgedMemoryAddress() && addr >= mem.getBridgedMemoryAddress())
            translated = dsp56k::MemArea_P;
        require(addr < mem.size(translated), "DSP address outside allocated memory");
        return mem.get(area, addr);
    }
    std::string registers(int cpu) {
        Object o;
        o.text("cpu", cpuName(cpu));
        o.num("boundary_pc", pcs[cpu]);
        o.num("observed_instructions", instructions[cpu]);
        o.num("cycles", cycles(cpu));
        o.boolean("at_instruction_boundary", instructionBoundary[cpu]);
        if (cpu == 0) {
            auto &uc = hw->getUC();
            auto *state = uc.getCpuState();
            o.num("pc", uc.getPC());
            for (unsigned i = 0; i < 8; ++i) {
                o.num("d" + std::to_string(i), uc.getDReg(i));
                o.num("a" + std::to_string(i), uc.getAReg(i));
            }
            o.num("sr", m68k_get_reg(state, M68K_REG_SR));
            o.num("vbr", state->vbr);
            o.num("cacr", state->cacr);
            o.num("mbar", state->cf_mbar);
            o.num("rambar", state->cf_rambar);
            o.num("acr0", state->cf_acr0);
            o.num("acr1", state->cf_acr1);
        } else {
            auto &d = dsp(cpu);
            const auto &r = d.regs();
            o.num("pc", instructionBoundary[cpu] ? pcs[cpu] : r.pc.toWord());
            o.num("backend_pc", r.pc.toWord());
            o.num("sr", d.getSR().toWord());
            o.num("omr", r.omr.toWord());
            o.num("x0", d.x0().toWord());
            o.num("x1", d.x1().toWord());
            o.num("y0", d.y0().toWord());
            o.num("y1", d.y1().toWord());
            // Store 56-bit accumulators as hexadecimal strings: JSON clients may use binary64.
            auto hex = [](uint64_t v) {
                std::ostringstream s;
                s << "0x" << std::hex << v;
                return s.str();
            };
            o.text("a", hex(uint64_t(r.a.var) >> 8));
            o.text("b", hex(uint64_t(r.b.var) >> 8));
            o.num("la", r.la.toWord());
            o.num("lc", r.lc.toWord());
            o.num("sp", r.sp.toWord());
            o.num("sc", r.sc.var);
            o.num("vba", r.vba.toWord());
            for (unsigned i = 0; i < 8; ++i) {
                o.num("r" + std::to_string(i), r.r[i].toWord());
                o.num("n" + std::to_string(i), r.n[i].toWord());
                o.num("m" + std::to_string(i), r.m[i].toWord());
            }
            std::vector<std::string> stack;
            for (const auto &v : r.ss)
                stack.push_back(quote(hex(v.var)));
            o.raw("system_stack", array(stack));
            o.text("cycle_precision", "JIT block accounting; observed_instructions counts instruction hooks, "
                                      "including REP iterations");
        }
        return o.str();
    }
    std::string status() {
        Object o;
        o.boolean("paused", paused);
        o.text("selected", cpuName(selected));
        if (!paused)
            return o.str();
        o.text("reason", reason);
        o.text("stopped_cpu", cpuName(stoppedCpu));
        o.num("frames", frames);
        if (!captureFile.empty()) {
            Object a;
            a.text("file", captureFile);
            a.num("frames", capture.size() / captureChannels);
            a.num("channels", captureChannels);
            a.num("remaining", captureRemaining);
            a.real("peak", capturePeak);
            a.real("rms", capture.empty() ? 0 : std::sqrt(captureEnergy / capture.size()));
            o.raw("audio", a.str());
        }
        o.num("hit_id", hitId);
        if (reason == "watchpoint")
            o.raw("access", hitEvent.json());
        o.boolean("dsp_ready", hw->isAudioReady());
        o.boolean("midi_ready", hw->isFirmwareMidiReady());
        o.num("lcd_pixels", hw->getFrontPanelSnapshot().countLitPixels());
        o.boolean("task_workaround_enabled", hw->getUC().debugTaskWorkaround);
        o.num("task_workaround_calls", workaroundCalls);
        std::vector<std::string> processors;
        for (int i = 0; i < 3; ++i)
            processors.push_back(registers(i));
        o.raw("processors", array(processors));
        if (!workerError.empty())
            o.text("error", workerError);
        return o.str();
    }
    std::string peripherals() {
        Object o;
        auto &uc = hw->getUC();
        auto &sim = uc.getSim();
        o.num("mbar", sim.getMbar());
        o.num("midi_rx_queued", uc.queuedMidiRxBytes());
        o.num("midi_rx_consumed", uc.midiRxConsumedCount());
        o.num("panel_rx_free_bytes", uc.availablePanelRxBytes());
        o.num("parallel_direction", sim.getParallelDirection());
        o.num("parallel_data", sim.getParallelData());
        o.num("dsp1_host_rx_words", uc.getHdi08Dsp1().hostRxWordsAvailable());
        o.num("dsp2_host_rx_words", uc.getHdi08Dsp2().hostRxWordsAvailable());
        o.num("dsp1_icr", uc.getHdi08Dsp1().icr());
        o.num("dsp2_icr", uc.getHdi08Dsp2().icr());
        for (int i = 1; i <= 2; ++i) {
            auto &per = i == 1 ? hw->getDspMixer().getPeriph() : hw->getDspProducer().getPeriph();
            Object p;
            std::vector<std::string> dma;
            for (unsigned c = 0; c < 6; ++c) {
                Object d;
                d.num("channel", c);
                d.num("source", per.getDMA().getDSR(c));
                d.num("destination", per.getDMA().getDDR(c));
                d.num("count", per.getDMA().getDCO(c));
                d.num("control", per.getDMA().getDCR(c));
                dma.push_back(d.str());
            }
            p.raw("dma", array(dma));
            o.raw(cpuName(i), p.str());
        }
        o.text("semantics", "Direct state snapshots. No MMIO reads, queue drains, interrupt "
                            "acknowledgements, or DSP catch-up.");
        return o.str();
    }

  public:
    explicit Monitor(const std::vector<uint8_t> &rom, bool experimental = false) {
        require((experimental && rom.size() == 0x800000) ||
                md::RomLoader::isRomForModel(rom, md::MachineModel::Machinedrum),
                "Unsupported firmware fingerprint");
        if (experimental) md::RomLoader::allowExperimentalImage(rom);
        hw = std::make_unique<md::Hardware>(rom, "monitor");
        require(hw->getUC().copyFlashData() == rom,
                "Backend did not load the exact requested firmware image");
        active = this;
        m68k_set_instr_hook_callback(hw->getUC().getCpuState(), cpuHook);
        hw->getUC().debugAccess = [this](bool w, uint32_t a, uint32_t v, unsigned n) {
            access(0, w, 0, a, v, n);
        };
        hw->getUC().debugEvent = [this](const char *k, uint32_t a, uint32_t v) { event(0, k, a, v); };
        for (int i = 1; i <= 2; ++i) {
            auto &d = dsp(i);
            d.debugInstruction = [this, i](uint32_t pc) { instruction(i, pc); };
            d.debugAccess = [this, i](bool w, dsp56k::EMemArea s, uint32_t a, uint32_t v) {
                access(i, w, s, a, v, 1);
            };
            d.debugEvent = [this, i](const char *k, uint32_t a, uint32_t v) { event(i, k, a, v); };
            d.debugTransfer = [this, i](const char *k, dsp56k::EMemArea src, uint32_t from,
                                        dsp56k::EMemArea dst, uint32_t to, uint32_t count) {
                if (trace == Trace::Off || trace == Trace::Program ||
                    (trace == Trace::Cpu && i != traceCpu))
                    return;
                Event e{0, 0, i, src, k, pcs[i], from, 0, 0, false};
                e.transfer = true;
                e.destinationSpace = dst;
                e.destination = to;
                e.transferCount = count;
                record(e);
            };
            auto config = d.getJit().getConfig();
            config.memoryWritesCallCpp = true;
            // Use single-instruction blocks so publication cannot change longer-block
            // optimizations. The JIT hook temporarily publishes and then restores backing state.
            // One-instruction blocks are mandatory; REP bodies still have individual hooks.
            config.maxInstructionsPerBlock = 1;
            config.enableOptimizer = false;
            config.linkJitBlocks = false;
            d.getJit().setConfig(config);
        }
        worker = std::thread([this] { runWorker(); });
        std::unique_lock lock(mutex);
        cv.wait(lock, [&] { return paused; });
    }
    ~Monitor() {
        {
            std::lock_guard lock(mutex);
            quitting = true;
            paused = false;
            cv.notify_all();
        }
        worker.join();
        active = nullptr;
    }
    std::string command(const std::string &line) {
        auto t = tokenize(line);
        require(!t.empty(), "Empty command");
        const auto &cmd = t[0];
        std::unique_lock lock(mutex);
        auto arity = [&](size_t low, size_t high) {
            require(t.size() >= low && t.size() <= high, "Wrong number of arguments for " + cmd);
        };
        if (cmd == "status") {
            arity(1, 1);
            return status();
        }
        if (cmd == "pause" || cmd == "wait") {
            arity(1, 2);
            if (cmd == "pause")
                pauseRequested = true;
            auto ms = t.size() == 2 ? number(t[1], 600000) : 30000;
            cv.wait_for(lock, std::chrono::milliseconds(ms), [&] { return paused; });
            return status();
        }
        needPaused();
        if (cmd == "cpu") {
            arity(2, 2);
            selected = cpuNumber(t[1]);
            return status();
        }
        if (cmd == "regs") {
            arity(1, 2);
            return registers(t.size() == 2 ? cpuNumber(t[1]) : selected);
        }
        if (cmd == "audio-budget") {
            arity(2,2);
            if(t[1]=="reset") {audioBudget=AudioBudget{};audioBudget.enabled=true;}
            else if(t[1]=="off") audioBudget.enabled=false;
            else require(t[1]=="status","audio-budget reset|status|off");
            return budgetStatus();
        }
        if (cmd == "pclog") {
            require(t.size() >= 2, "pclog add CPU PC...|clear|status|dump FILE");
            if (t[1] == "add") {
                require(t.size() >= 4, "pclog add CPU PC...");
                int cpu = cpuNumber(t[2]);
                for (size_t i = 3; i < t.size(); ++i) pcLogSet.insert((uint64_t(cpu) << 32) | number(t[i], 0xffffff));
            } else if (t[1] == "sample") {
                arity(4, 4);
                pcSample[cpuNumber(t[2])] = number(t[3], 1u << 30);
            } else if (t[1] == "clear") {
                pcLogSet.clear(); pcLog.clear(); pcSample = {};
            } else if (t[1] == "reset") {
                pcLog.clear();
            } else if (t[1] == "dump") {
                arity(3, 3);
                std::ofstream out(t[2]);
                require(bool(out), "Cannot open pclog output");
                for (const auto &r : pcLog)
                    out << r.cpu << ' ' << r.pc << ' ' << r.c1 << ' ' << r.c2 << ' ' << r.ddr0 << ' ' << r.ddr4 << ' ' << r.r6 << ' ' << r.c0 << '\n';
            } else require(t[1] == "status", "pclog add CPU PC...|sample CPU N|clear|reset|status|dump FILE");
            Object o; o.num("watched", pcLogSet.size()); o.num("records", pcLog.size()); o.num("capacity", pcLogCap);
            return o.str();
        }
        if (cmd == "peripherals") {
            arity(1, 1);
            return peripherals();
        }
        if (cmd == "input") {
            arity(2, 5);
            require(!captureRemaining, "Finish recording before changing the input source");
            if (t[1] == "off") {
                arity(2, 2);
                inputHz = 0;
            } else {
                require(t[1] == "tone", "Use input off or input tone HZ LEVEL A|B|both");
                arity(5, 5);
                auto realNumber = [](const std::string& value) {
                    size_t used = 0;
                    const double v = std::stod(value, &used);
                    require(used == value.size() && std::isfinite(v), "Invalid finite number");
                    return v;
                };
                const double hz = realNumber(t[2]), level = realNumber(t[3]);
                require(hz > 0 && hz < 22050 && level >= 0 && level <= 1, "Input tone needs 0 < Hz < 22050 and 0 <= level <= 1");
                require(t[4] == "A" || t[4] == "B" || t[4] == "both", "Input channel must be A, B or both");
                inputHz = hz; inputLevel = level; inputPhase = 0;
                inputMask = t[4] == "A" ? 1 : t[4] == "B" ? 2 : 3;
            }
            return "{}";
        }
        if (cmd == "record") {
            arity(3, 4);
            require(!captureRemaining, "Recording in progress; continue to finish it");
            require(hw->isAudioReady(), "DSPs are not ready; boot first");
            auto n = number(t[2], 44100 * 30);
            require(n > 0, "Capture length must be positive");
            const auto requestedChannels = t.size() == 4 ? number(t[3], 6) : 2;
            require(requestedChannels == 2 || requestedChannels == 6, "Capture channels must be 2 or 6");
            captureChannels = static_cast<unsigned>(requestedChannels);
            captureFile = t[1];
            capture.clear();
            capture.reserve(n * captureChannels);
            capturePeak = 0;
            captureEnergy = 0;
            captureRemaining = n;
            audioPrimed = false;
            deadline = 0;
            stepCpu = -1;
            bootRequested = false;
            hitId = 0;
            reason = "running";
            pauseRequested = false;
            paused = false;
            cv.notify_all();
            cv.wait_for(lock, std::chrono::seconds(60), [&] { return paused; });
            return status();
        }
        if (cmd == "run" || cmd == "continue" || cmd == "step" || cmd == "boot") {
            arity(1, 2);
            require(workerError.empty(), workerError);
            hitId = 0;
            auto budget = t.size() == 2 ? number(t[1], 44100 * 120) : 44100 * 20;
            require(budget > 0, "Budget/count must be positive");
            stepCpu = cmd == "step" ? selected : -1;
            if (cmd == "step") {
                stepTarget = instructions[selected] + (t.size() == 2 ? budget : 1);
                budget = 44100 * 20;
            }
            bootRequested = cmd == "boot";
            deadline = frames + budget;
            reason = "running";
            pauseRequested = false;
            paused = false;
            cv.notify_all();
            if (cmd != "run")
                cv.wait_for(lock, std::chrono::seconds(60), [&] { return paused; });
            return status();
        }
        if (cmd == "memory" || cmd == "dump") {
            arity(cmd == "dump" ? 5 : 4, cmd == "dump" ? 5 : 4);
            int space = spaceNumber(t[1], selected);
            uint32_t start = address(t[2], selected, space);
            auto count = number(t[3], cmd == "dump" ? 0x200000 : 4096);
            require(count > 0 && uint64_t(start) + count <= (selected == 0 ? 0x100000000ull : 0x1000000ull),
                    "Invalid memory range");
            std::vector<std::string> values;
            std::vector<uint8_t> bytes;
            for (uint64_t i = 0; i < count; ++i) {
                auto v = peek(selected, space, start + i);
                if (cmd == "memory")
                    values.push_back(std::to_string(v));
                else if (selected == 0)
                    bytes.push_back(v);
                else {
                    bytes.push_back(v);
                    bytes.push_back(v >> 8);
                    bytes.push_back(v >> 16);
                }
            }
            Object o;
            o.text("cpu", cpuName(selected));
            o.text("space", t[1]);
            o.num("address", start);
            o.num("count", count);
            if (cmd == "dump") {
                std::ofstream f(t[4], std::ios::binary);
                f.write(reinterpret_cast<char *>(bytes.data()), bytes.size());
                require(bool(f), "Cannot write dump");
                o.text("file", t[4]);
            } else
                o.raw("values", array(values));
            return o.str();
        }
        if (cmd == "disasm") {
            arity(1, 3);
            uint32_t pc =
                t.size() > 1
                    ? address(t[1], selected, 0)
                    : (selected == 0 ? hw->getUC().getPC()
                                     : (instructionBoundary[selected] ? pcs[selected]
                                                                      : dsp(selected).getPC().toWord()));
            auto count = t.size() > 2 ? number(t[2], 256) : 8;
            require(count > 0, "Count must be positive");
            std::vector<std::string> lines;
            if (selected == 0) {
                require(!(pc & 1), "ColdFire instruction address must be even");
                std::vector<uint8_t> bytes;
                for (unsigned i = 0; i < count * 12 + 12; ++i) {
                    uint8_t v;
                    if (uint64_t(pc) + i > UINT32_MAX || !hw->getUC().debugPeek(pc + i, v))
                        break;
                    bytes.push_back(v);
                }
                require(bytes.size() >= 2, "Cannot disassemble MMIO/unmapped memory");
                auto decoded = decodeColdFire(pc, bytes);
                for (size_t i = 0; i < std::min<size_t>(count, decoded.size()); ++i) {
                    const auto &d = decoded[i];
                    Object o;
                    o.num("address", d.address);
                    o.num("size", d.size);
                    o.text("text", d.text);
                    o.text("symbol", label(0, 0, d.address));
                    o.text("decoder", "GNU m68k:isa-a:mac");
                    lines.push_back(o.str());
                }
                return array(lines);
            }
            for (unsigned i = 0; i < count; ++i) {
                Object o;
                o.num("address", pc);
                o.text("symbol", label(selected, 0, pc));
                std::string text;
                unsigned size;
                {
                    auto a = peek(selected, 0, pc);
                    auto b = peek(selected, 0, pc + 1);
                    auto &d = dsp(selected);
                    dsp56k::Disassembler::Line decoded;
                    size = d.disassembler().disassemble(decoded, a, b, d.getSR().toWord(),
                                                        d.regs().omr.toWord(), pc);
                    text = dsp56k::Disassembler::formatLine(decoded);
                    o.boolean("valid", size != 0);
                    o.raw("words", size == 2 ? "[" + std::to_string(a) + "," + std::to_string(b) + "]"
                                             : "[" + std::to_string(a) + "]");
                    if (size != 0 && decoded.instA >= 0 && decoded.instA < dsp56k::ResolveCache) {
                        const auto &info = dsp56k::g_opcodes[decoded.instA];
                        o.num("opcode_flags", info.m_flags);
                        o.boolean("branch", info.flag(dsp56k::OpFlagBranch));
                        o.boolean("conditional", info.flag(dsp56k::OpFlagCondition));
                        o.boolean("call", info.flag(dsp56k::OpFlagBranch) && info.flag(dsp56k::OpFlagPushPC));
                        o.boolean("return", info.flag(dsp56k::OpFlagPopPC));
                        if (info.flag(dsp56k::OpFlagBranch)) {
                            auto target = dsp56k::getBranchTarget(decoded.instA, a, b, pc);
                            o.boolean("dynamic_target", target == dsp56k::g_dynamicAddress);
                            if (target != dsp56k::g_dynamicAddress && target != dsp56k::g_invalidAddress)
                                o.num("target", target & 0xffffff);
                        }
                        if (info.flag(dsp56k::OpFlagDo))
                            o.num("loop_end", (info.m_extensionWordType & dsp56k::PCRelativeAddressExt)
                                                  ? (pc + b) & 0xffffff : b);
                    }
                    if (size == 0)
                        size = 1;
                }
                o.text("text", text);
                o.num("size", size);
                lines.push_back(o.str());
                require(uint64_t(pc) + size <= (selected == 0 ? UINT32_MAX : 0xffffff),
                        "Disassembly address overflow");
                pc += size;
            }
            return array(lines);
        }
        if (cmd == "break") {
            arity(2, 2);
            auto a = address(t[1], selected, 0);
            require(selected != 0 || !(a & 1), "ColdFire breakpoint must be even");
            uint64_t key = (uint64_t(selected) << 32) | a;
            require(!breakIndex.count(key), "Breakpoint already exists");
            auto id = nextId++;
            breaks.push_back({id, selected, a});
            breakIndex[key] = id;
            Object o;
            o.num("id", id);
            o.num("address", a);
            return o.str();
        }
        if (cmd == "watch") {
            arity(4, 5);
            require(t[1] == "r" || t[1] == "w" || t[1] == "rw", "Watch mode must be r, w or rw");
            int space = spaceNumber(t[2], selected);
            auto a = address(t[3], selected, space);
            auto count = t.size() > 4 ? number(t[4], 0x1000000) : 1;
            require(count > 0 && uint64_t(a) + count <= (selected == 0 ? 0x100000000ull : 0x1000000ull),
                    "Invalid watch range");
            auto id = nextId++;
            watches.push_back({id, selected, space, a, static_cast<uint32_t>(count), t[1]});
            Object o;
            o.num("id", id);
            return o.str();
        }
        if (cmd == "delete") {
            arity(2, 2);
            auto id = number(t[1]);
            bool found = false;
            for (auto it = breaks.begin(); it != breaks.end();)
                if (it->id == id) {
                    breakIndex.erase((uint64_t(it->cpu) << 32) | it->address);
                    it = breaks.erase(it);
                    found = true;
                } else
                    ++it;
            for (auto it = watches.begin(); it != watches.end();)
                if (it->id == id) {
                    it = watches.erase(it);
                    found = true;
                } else
                    ++it;
            require(found, "No such breakpoint/watchpoint");
            for (int i = 0; i < 3; ++i)
                if (watchPending[i] && watchIds[i] == id)
                    watchPending[i] = false;
            return "{}";
        }
        if (cmd == "points") {
            arity(1, 1);
            std::vector<std::string> rows;
            for (const auto &b : breaks) {
                Object o;
                o.num("id", b.id);
                o.text("type", "breakpoint");
                o.text("cpu", cpuName(b.cpu));
                o.num("address", b.address);
                rows.push_back(o.str());
            }
            for (const auto &w : watches) {
                Object o;
                o.num("id", w.id);
                o.text("type", "watchpoint");
                o.text("cpu", cpuName(w.cpu));
                o.text("space", w.cpu == 0 ? "B" : std::string(1, "PXY"[w.space]));
                o.num("address", w.address);
                o.num("count", w.count);
                o.text("mode", w.mode);
                rows.push_back(o.str());
            }
            return array(rows);
        }
        if (cmd == "trace") {
            arity(2, 3);
            if (t[1] == "off")
                trace = Trace::Off;
            else if (t[1] == "io")
                trace = Trace::Io;
            else if (t[1] == "all")
                trace = Trace::All;
            else if (t[1] == "cpu") {
                trace = Trace::Cpu;
                traceCpu = selected;
            } else if (t[1] == "program") {
                require(selected != 0, "Select a DSP first");
                trace = Trace::Program;
                traceCpu = selected;
            }
            else if (t[1] == "clear") {
                eventsWritten = 0;
                eventSequence = 0;
            } else if (t[1] == "save") {
                require(t.size() == 3, "trace save FILE");
                std::ofstream file(t[2]);
                for (uint64_t i = eventsWritten > capacity ? eventsWritten - capacity : 0; i < eventsWritten;
                     ++i)
                    file << ring[i % capacity].json() << '\n';
                require(bool(file), "Cannot write trace");
            } else
                throw std::runtime_error("trace off|io|all|program|cpu|clear|save FILE");
            Object o;
            o.num("retained", std::min<uint64_t>(capacity, eventsWritten));
            o.num("dropped", eventsWritten > capacity ? eventsWritten - capacity : 0);
            return o.str();
        }
        if (cmd == "events") {
            arity(1, 2);
            auto n = t.size() == 2 ? number(t[1], capacity) : 64;
            std::vector<std::string> rows;
            for (uint64_t i = eventsWritten > n ? eventsWritten - n : 0; i < eventsWritten; ++i)
                rows.push_back(ring[i % capacity].json());
            return array(rows);
        }
        if (cmd == "symbol") {
            arity(4, 4);
            int space = spaceNumber(t[1], selected);
            auto a = address(t[2], selected, space);
            symbols.push_back({selected, space, a, t[3], "manual"});
            return "{}";
        }
        if (cmd == "symbols") {
            arity(1, 3);
            if (t.size() > 1) {
                require(t.size() == 3 && t[1] == "load", "symbols load FILE");
                std::ifstream file(t[2]);
                require(bool(file), "Cannot open symbol file");
                std::string row;
                std::vector<Symbol> added;
                while (std::getline(file, row)) {
                    if (row.empty() || row[0] == '#')
                        continue;
                    auto fields = tokenize(row);
                    require(fields.size() >= 4, "Symbol row: CPU SPACE ADDRESS NAME [PROVENANCE]");
                    int cpu = cpuNumber(fields[0]), space = spaceNumber(fields[1], cpu);
                    auto a = static_cast<uint32_t>(number(fields[2], cpu == 0 ? UINT32_MAX : 0xffffff));
                    added.push_back({cpu, space, a, fields[3], fields.size() > 4 ? fields[4] : "imported"});
                }
                symbols.insert(symbols.end(), added.begin(), added.end());
            }
            std::vector<std::string> rows;
            for (const auto &s : symbols) {
                Object o;
                o.text("cpu", cpuName(s.cpu));
                o.text("space", s.cpu == 0 ? "B" : std::string(1, "PXY"[s.space]));
                o.num("address", s.address);
                o.text("name", s.name);
                o.text("provenance", s.provenance);
                rows.push_back(o.str());
            }
            return array(rows);
        }
        if (cmd == "workaround") {
            arity(2, 2);
            require(t[1] == "on" || t[1] == "off", "workaround on|off");
            hw->getUC().debugTaskWorkaround = t[1] == "on";
            return status();
        }
        if (cmd == "midi-out") {
            arity(1, 1);
            std::vector<synthLib::SMidiEvent> messages;
            hw->readMidiOut(messages);
            std::vector<std::string> rows;
            for (const auto &m : messages) {
                Object o;
                o.num("status", m.a);
                o.num("data1", m.b);
                o.num("data2", m.c);
                std::vector<std::string> bytes;
                for (auto b : m.sysex)
                    bytes.push_back(std::to_string(b));
                o.raw("sysex", array(bytes));
                rows.push_back(o.str());
            }
            return array(rows);
        }
        if (cmd == "midi") {
            arity(2, 257);
            std::vector<uint8_t> bytes;
            for (size_t i = 1; i < t.size(); ++i)
                bytes.push_back(number(t[i], 255));
            synthLib::SMidiEvent e(synthLib::MidiEventSource::Host);
            e.assignRawData(bytes.data(), bytes.size(), synthLib::MidiEventSource::Host, 0);
            require(hw->sendMidi(e), "MIDI queue rejected message");
            return "{}";
        }
        if (cmd == "panel") {
            arity(3, 3);
            require(hw->trySendPanelEvent(number(t[1], 255), number(t[2], 255)),
                    "Panel queue rejected event");
            return "{}";
        }
        if (cmd == "lcd") {
            arity(2, 2);
            std::ofstream f(t[1]);
            auto p = hw->getFrontPanelSnapshot();
            f << "P1\n128 64\n";
            for (unsigned y = 0; y < 64; ++y) {
                for (unsigned x = 0; x < 128; ++x)
                    f << p.getLcdPixel(x, y) << ' ';
                f << '\n';
            }
            require(bool(f), "Cannot write LCD");
            return "{}";
        }
        throw std::runtime_error("Unknown command: " + cmd);
    }
};
} // namespace

int main(int argc, char **argv) {
    if (argc != 2 && !(argc == 3 && std::string(argv[2]) == "--experimental-firmware")) {
        std::cerr << "Usage: md-monitor FIRMWARE [--experimental-firmware] (stdin commands, stdout JSONL)\n";
        return 2;
    }
    // The dependencies print diagnostics to stdout. Keep a dedicated protocol descriptor.
    FILE *protocol = fdopen(dup(STDOUT_FILENO), "w");
    if (!protocol || dup2(STDERR_FILENO, STDOUT_FILENO) < 0)
        return 2;
    std::signal(SIGINT, signalPause);
    try {
        std::vector<uint8_t> rom;
        require(baseLib::filesystem::readFile(rom, argv[1]), "Cannot read firmware");
        Monitor monitor(rom, argc == 3);
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line == "quit") {
                std::fprintf(protocol, "{\"ok\":true,\"result\":{\"quit\":true}}\n");
                std::fflush(protocol);
                break;
            }
            try {
                auto result = monitor.command(line);
                std::fprintf(protocol, "{\"ok\":true,\"result\":%s}\n", result.c_str());
            } catch (const std::exception &e) {
                std::fprintf(protocol, "{\"ok\":false,\"error\":%s}\n", quote(e.what()).c_str());
            }
            std::fflush(protocol);
        }
    } catch (const std::exception &e) {
        std::fprintf(protocol, "{\"ok\":false,\"error\":%s}\n", quote(e.what()).c_str());
        std::fflush(protocol);
        return 1;
    }
    std::fclose(protocol);
    return 0;
}
