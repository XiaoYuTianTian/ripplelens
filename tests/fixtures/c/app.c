#include "lib.h"

int a(void) {
    return helper(1);
}

int b(void) {
    return helper(2) + a();
}

int register_fn(int (*f)(int)) {
    return f(3);
}

int c(void) {
    return register_fn(helper);
}

double d(void) {
    struct shape s = {circle_area, 1};
    return circle_area(&s);
}

double e(void) {
    struct shape s = {circle_area, 2};
    return total(&s, 1);
}
