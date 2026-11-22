#include "lib.hpp"

namespace app {

int a() {
    return fx::helper(1);
}

int b() {
    return fx::helper(2) + a();
}

int register_fn(int (*f)(int)) {
    return f(3);
}

int c() {
    return register_fn(fx::helper);
}

double d() {
    return fx::Circle(1).area();
}

double e() {
    fx::Circle circle(2);
    const fx::Shape *shapes[] = {&circle};
    return fx::total(shapes, 1);
}

}  // namespace app
