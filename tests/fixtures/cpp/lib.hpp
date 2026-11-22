#pragma once

namespace fx {

constexpr int kLimit = 10;

class Shape {
public:
    virtual ~Shape() = default;
    virtual double area() const = 0;
};

class Circle : public Shape {
public:
    explicit Circle(double r) : r_(r) {}
    double area() const override;

private:
    double r_;
};

int helper(int x);
double total(const Shape *const *shapes, int n);

}  // namespace fx
