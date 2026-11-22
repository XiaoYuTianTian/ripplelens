#ifndef FX_LIB_H
#define FX_LIB_H

#define LIMIT 10

extern const int limit_value;

int helper(int x);

typedef double (*area_fn)(const void *self);

struct shape {
    area_fn area;
    double r;
};

double circle_area(const void *self);
double total(const struct shape *shapes, int n);

#endif
