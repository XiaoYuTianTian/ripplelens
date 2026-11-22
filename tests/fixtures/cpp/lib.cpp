#include "lib.hpp"

namespace fx {

double Circle::area() const {
    return 3.14 * r_ * r_;
}

int helper(int x) {
    return x + kLimit;
}

double total(const Shape *const *shapes, int n) {
    double t = 0;
    for (int i = 0; i < n; i++) {
        t += shapes[i]->area();
    }
    return t;
}

}  // namespace fx
