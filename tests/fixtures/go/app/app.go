package app

import "example.com/fx/lib"

func A() int {
	return lib.Helper(1)
}

func B() int {
	return lib.Helper(2) + A()
}

func Register(f func(int) int) int {
	return f(3)
}

func C() int {
	return Register(lib.Helper)
}

func D() float64 {
	return lib.Circle{R: 1}.Area()
}

func E() float64 {
	return lib.Total([]lib.Shape{lib.Circle{R: 2}})
}
