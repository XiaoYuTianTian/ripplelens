package fx.app;

import fx.lib.Circle;
import fx.lib.Lib;
import java.util.List;
import java.util.function.IntUnaryOperator;

public final class App {
    private App() {}

    public static int a() {
        return Lib.helper(1);
    }

    public static int b() {
        return Lib.helper(2) + a();
    }

    public static int register(IntUnaryOperator f) {
        return f.applyAsInt(3);
    }

    public static int c() {
        return register(Lib::helper);
    }

    public static double d() {
        return new Circle(1).area();
    }

    public static double e() {
        return Lib.total(List.of(new Circle(2)));
    }
}
