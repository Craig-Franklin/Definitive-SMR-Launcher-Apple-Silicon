/* Original synthetic fixture; no game code or assets. MIT, Craig Franklin 2026. */
#include <stdint.h>
#include <stdio.h>
__attribute__((noinline)) uint32_t bounded_index(uint32_t value) {
    return value < 7 ? value : 0;
}
int main(void) {
    puts("SMR research synthetic fixture");
    return bounded_index(9);
}
