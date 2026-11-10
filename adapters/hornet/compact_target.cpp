#include <charconv>
#include <cstdint>
#include <iostream>
#include <string_view>

#include "hornetlib/protocol/compact_target.h"

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::string_view input{argv[1]};
    std::uint32_t bits{};
    const auto [end, error] = std::from_chars(input.begin(), input.end(), bits, 16);
    if (input.size() != 8 || error != std::errc{} || end != input.end()) return 2;

    const auto target = hornet::protocol::CompactTarget{bits}.Expand();
    std::cout << "{\"target\":" << target.Value() << ",\"work\":" << target.GetWork() << "}\n";
}
