package fx.lib;

import java.util.List;

public final class Lib {
    public static final int LIMIT = 10;

    private Lib() {}

    public static int helper(int x) {
        return x + LIMIT;
    }

    public static double total(List<Shape> shapes) {
        double t = 0;
        for (Shape s : shapes) {
            t += s.area();
        }
        return t;
    }
}
