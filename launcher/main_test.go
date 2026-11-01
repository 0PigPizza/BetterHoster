package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPrepareIncludesAppAndPreservesUserEnv(t *testing.T) {
	dir := t.TempDir()
	env := filepath.Join(dir, ".env")
	if err := os.WriteFile(env, []byte("CUSTOM=value\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := prepare(dir); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"compose.yaml", "Dockerfile", ".dockerignore", "server.py", "server-dashboard.html", "schema.sql"} {
		if info, err := os.Stat(filepath.Join(dir, name)); err != nil || info.Size() == 0 {
			t.Fatalf("missing bundled file %s: %v", name, err)
		}
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(filepath.Join(dir, "schema.sql"))
		if err != nil || info.Mode().Perm()&0004 == 0 {
			t.Fatal("schema.sql is not readable by the database container")
		}
	}
	value, err := os.ReadFile(env)
	if err != nil || string(value) != "CUSTOM=value\n" {
		t.Fatal("user .env was changed")
	}
}
