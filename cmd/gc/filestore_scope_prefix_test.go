package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gastownhall/gascity/internal/beads"
)

// writeScopedFileStoreCity lays out a scoped-layout file-store city with one
// rig whose routes.jsonl names the rig prefix, mirroring what `gc rig add`
// writes for a GC_BEADS=file city.
func writeScopedFileStoreCity(t *testing.T) (cityPath, rigPath string) {
	t.Helper()
	root := t.TempDir()
	cityPath = filepath.Join(root, "city")
	rigPath = filepath.Join(root, "rigs", "demo")
	for _, dir := range []string{
		filepath.Join(cityPath, ".gc"),
		filepath.Join(cityPath, ".beads"),
		filepath.Join(rigPath, ".gc"),
		filepath.Join(rigPath, ".beads"),
	} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(cityPath, "city.toml"), []byte("[workspace]\nname = \"factory\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ensureScopedFileStoreLayout(cityPath); err != nil {
		t.Fatal(err)
	}
	for _, scope := range []string{cityPath, rigPath} {
		if err := ensurePersistedScopeLocalFileStore(scope); err != nil {
			t.Fatal(err)
		}
	}
	if err := writeAllRoutes([]rigRoute{{Prefix: "fa", AbsDir: cityPath}, {Prefix: "de", AbsDir: rigPath}}); err != nil {
		t.Fatal(err)
	}
	return cityPath, rigPath
}

// TestRigScopedFileStoreMintsRoutedPrefix pins the id-namespace contract the
// by-id resolvers rely on: a rig's file store must mint ids under the prefix
// its routes.jsonl advertises, not the city-wide "gc" default, or a rig bead
// and a city bead can share an id and every by-id read resolves the wrong one.
func TestRigScopedFileStoreMintsRoutedPrefix(t *testing.T) {
	cityPath, rigPath := writeScopedFileStoreCity(t)

	rigStore, err := openCompatibleFileStore(rigPath, cityPath)
	if err != nil {
		t.Fatalf("open rig store: %v", err)
	}
	bead, err := rigStore.Create(beads.Bead{Title: "rig work"})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if !strings.HasPrefix(bead.ID, "de-") {
		t.Fatalf("rig bead id = %q, want the routed rig prefix de-", bead.ID)
	}
}

// TestCityScopedFileStoreKeepsDefaultPrefix is the control: the city (HQ)
// store keeps minting under the default prefix so existing file-store cities
// do not change id shape when a rig is added.
func TestCityScopedFileStoreKeepsDefaultPrefix(t *testing.T) {
	cityPath, _ := writeScopedFileStoreCity(t)

	cityStore, err := openCompatibleFileStore(cityPath, cityPath)
	if err != nil {
		t.Fatalf("open city store: %v", err)
	}
	bead, err := cityStore.Create(beads.Bead{Title: "city work"})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if !strings.HasPrefix(bead.ID, "gc-") {
		t.Fatalf("city bead id = %q, want the default gc- prefix", bead.ID)
	}
}

// TestRigScopedFileStoreWithoutRoutesKeepsDefaultPrefix covers a rig that has
// no routes.jsonl yet (a scope opened before `gc rig add` wrote routes): it
// must still open and mint under the default prefix rather than fail.
func TestRigScopedFileStoreWithoutRoutesKeepsDefaultPrefix(t *testing.T) {
	cityPath, rigPath := writeScopedFileStoreCity(t)
	if err := os.Remove(filepath.Join(rigPath, ".beads", "routes.jsonl")); err != nil {
		t.Fatal(err)
	}

	rigStore, err := openCompatibleFileStore(rigPath, cityPath)
	if err != nil {
		t.Fatalf("open rig store: %v", err)
	}
	bead, err := rigStore.Create(beads.Bead{Title: "rig work"})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if !strings.HasPrefix(bead.ID, "gc-") {
		t.Fatalf("rig bead id = %q, want the default gc- prefix without routes", bead.ID)
	}
}
