// Package fixture is the smallest module go.yml passes: formatted, tidy, linted,
// fully covered and race-free. tests/go/test_go_check.py breaks it one way at a
// time and expects each check to fail.
package fixture

import "fmt"

// Lap is a lap time in milliseconds.
type Lap int

// String formats a lap as minutes, seconds and milliseconds.
func (l Lap) String() string {
	if l < 0 {
		return "invalid"
	}
	ms := int(l)
	return fmt.Sprintf("%d:%02d.%03d", ms/60000, ms/1000%60, ms%1000)
}

// Best returns the fastest of the laps, or false when there are none.
func Best(laps []Lap) (Lap, bool) {
	if len(laps) == 0 {
		return 0, false
	}
	best := laps[0]
	for _, l := range laps[1:] {
		if l < best {
			best = l
		}
	}
	return best, true
}
