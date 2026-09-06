package main

import "testing"

func TestRejectInvalidActions(t *testing.T) {
	for _, value := range []string{`{"action":"shell","command":"whoami"}`, `{"action":"click","x":-1,"y":0}`, `{"action":"click","x":1.5,"y":0}`, `{"action":"key","keys":[]}`, `{"action":"key","keys":["0123456789abc"]}`, `{"action":"scroll","amount":100}`, `{"action":"screenshot","host":"other"}`, `{"action":"screenshot"} {}`} {
		if _, e := decode([]byte(value)); e == nil {
			t.Fatalf("accepted %s", value)
		}
	}
}
func TestAcceptUnicodeAndCoordinates(t *testing.T) {
	for _, value := range []string{`{"action":"screenshot"}`, `{"action":"click","x":123,"y":456,"button":"right","count":2}`, `{"action":"type","text":"日本語🙂"}`, `{"action":"key","keys":["CTRL","A"]}`, `{"action":"key","keys":["CMD","SHIFT","="]}`, `{"action":"key","keys":["F5"]}`, `{"action":"key","keys":["CTRL","-"]}`} {
		if _, e := decode([]byte(value)); e != nil {
			t.Fatal(e)
		}
	}
}
