package lib

const Limit = 10

type Shape interface {
	Area() float64
}

type Circle struct {
	R float64
}

func (c Circle) Area() float64 {
	return 3.14 * c.R * c.R
}

func Helper(x int) int {
	return x + Limit
}

func Total(shapes []Shape) float64 {
	t := 0.0
	for _, s := range shapes {
		t += s.Area()
	}
	return t
}
