#pragma once
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <utility>
#include <vector>

struct DecodedInstruction {
    uint32_t address;
    unsigned size;
    std::string text;
};
// Invoke a real ISA-A/MAC decoder with literal argv. No shell or MMIO access.
inline std::vector<DecodedInstruction> decodeColdFire(uint32_t address, const std::vector<uint8_t> &bytes) {
    char inputName[] = "/tmp/md-code-XXXXXX", outputName[] = "/tmp/md-disasm-XXXXXX";
    int input = mkstemp(inputName), output = mkstemp(outputName);
    struct Cleanup {
        int a, b;
        const char *x, *y;
        ~Cleanup() {
            if (a >= 0)
                close(a);
            if (b >= 0)
                close(b);
            unlink(x);
            unlink(y);
        }
    } cleanup{input, output, inputName, outputName};
    if (input < 0 || output < 0)
        throw std::runtime_error("Cannot create disassembly scratch files");
    if (write(input, bytes.data(), bytes.size()) != static_cast<ssize_t>(bytes.size()))
        throw std::runtime_error("Cannot write disassembly buffer");
    std::string vma = "--adjust-vma=" + std::to_string(address);
    auto pid = fork();
    if (pid < 0)
        throw std::runtime_error("Cannot start ColdFire disassembler");
    if (pid == 0) {
        dup2(output, STDOUT_FILENO);
        dup2(output, STDERR_FILENO);
        execlp("m68k-linux-gnu-objdump", "m68k-linux-gnu-objdump", "-D", "-z", "-b", "binary", "-m",
               "m68k:isa-a:mac", "-EB", "--no-show-raw-insn", vma.c_str(), inputName,
               static_cast<char *>(nullptr));
        _exit(127);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0)
        if (errno != EINTR)
            throw std::runtime_error("Disassembler wait failed");
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        throw std::runtime_error("ColdFire decoder failed; install binutils-m68k-linux-gnu");
    lseek(output, 0, SEEK_SET);
    std::string listing;
    char buffer[4096];
    ssize_t n;
    while ((n = read(output, buffer, sizeof(buffer))) > 0)
        listing.append(buffer, n);
    std::istringstream lines(listing);
    std::string line;
    std::smatch match;
    std::regex pattern(R"(^\s*([0-9a-fA-F]+):\s+(.+)$)");
    std::vector<DecodedInstruction> result;
    while (std::getline(lines, line))
        if (std::regex_match(line, match, pattern)) {
            uint32_t pc = std::stoul(match[1].str(), nullptr, 16);
            if (!result.empty())
                result.back().size = pc - result.back().address;
            auto text = match[2].str();
            // GNU uses 68040 names for these ColdFire MOVEC register encodings.
            if (text.find("movec") != std::string::npos) {
                for (auto pair : {std::pair{"%itt0", "%acr0"}, std::pair{"%itt1", "%acr1"}}) {
                    auto at = text.find(pair.first);
                    if (at != std::string::npos)
                        text.replace(at, std::strlen(pair.first), pair.second);
                }
            }
            result.push_back({pc, 0, text});
        }
    if (!result.empty())
        result.back().size = address + bytes.size() - result.back().address;
    return result;
}
