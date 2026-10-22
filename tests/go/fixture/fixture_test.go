package fixture

import "testing"

func TestLapString(t *testing.T) {
	for _, c := range []struct {
		lap  Lap
		want string
	}{
		{83456, "1:23.456"},
		{5, "0:00.005"},
		{-1, "invalid"},
	} {
		if got := c.lap.String(); got != c.want {
			t.Errorf("Lap(%d).String() = %q, want %q", int(c.lap), got, c.want)
		}
	}
}

func TestBest(t *testing.T) {
	if _, ok := Best(nil); ok {
		t.Error("Best(nil) reported a lap")
	}
	if got, ok := Best([]Lap{90000, 83456, 85000}); !ok || got != 83456 {
		t.Errorf("Best = %d, %v; want 83456, true", int(got), ok)
	}
}
