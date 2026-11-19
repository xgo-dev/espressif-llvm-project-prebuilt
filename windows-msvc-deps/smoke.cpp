#include "llvm/ADT/SmallVector.h"
#include "llvm/Support/Compression.h"
#include "llvm/Support/Error.h"
#include <cstdio>
#include <cstring>

int main() {
  const char Text[] = "LLGo LLVM static compression dependencies: round trip";
  llvm::ArrayRef<uint8_t> Input(reinterpret_cast<const uint8_t *>(Text),
                               sizeof(Text));
  for (auto Format : {llvm::compression::Format::Zlib,
                      llvm::compression::Format::Zstd}) {
    if (llvm::compression::getReasonIfUnsupported(Format))
      return 1;
    llvm::SmallVector<uint8_t> Compressed, Output;
    llvm::compression::compress(llvm::compression::Params(Format), Input,
                                Compressed);
    if (auto E = llvm::compression::decompress(Format, Compressed, Output,
                                               Input.size())) {
      llvm::consumeError(std::move(E));
      return 2;
    }
    if (Output.size() != Input.size() ||
        std::memcmp(Input.data(), Output.data(), Input.size()))
      return 3;
  }
  std::puts("zlib and zstd LLVM round trips passed");
}
