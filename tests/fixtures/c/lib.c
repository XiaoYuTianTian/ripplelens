#include "lib.h"

const int limit_value = LIMIT;

int helper(int x) {
    return x + limit_value;
}

double circle_area(const void *self) {
    const struct shape *s = (const struct shape *)self;
    return 3.14 * s->r * s->r;
}

double total(const struct shape *shapes, int n) {
    double t = 0;
    for (int i = 0; i < n; i++) {
        t += shapes[i].area(&shapes[i]);
    }
    return t;
}
