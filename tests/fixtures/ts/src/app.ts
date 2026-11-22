import { Circle, helper, total } from "./lib";

export function a(): number {
  return helper(1);
}

export function b(): number {
  return helper(2) + a();
}

export function register(f: (x: number) => number): number {
  return f(3);
}

export function c(): number {
  return register(helper);
}

export function d(): number {
  return new Circle(1).area();
}

export function e(): number {
  return total([new Circle(2)]);
}
