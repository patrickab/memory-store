"""Terminal browser for every memory root. Interface layer: all reads and writes go through store.py."""

from __future__ import annotations

import glob
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, DataTable, Footer, Input, Label, ListItem, ListView, Markdown, Static, Tree

from memory_store import store
from memory_store.store import MemoryStoreError

ROOTS_FILE = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "memory-store" / "roots"


def load_roots(arguments: list[str]) -> list[Path]:
    """Roots from the arguments, else from ROOTS_FILE (one path or glob per line), else the default root."""
    patterns = arguments
    if not patterns and ROOTS_FILE.is_file():
        lines = (line.strip() for line in ROOTS_FILE.read_text().splitlines())
        patterns = [line for line in lines if line and not line.startswith("#")]
    if not patterns:
        patterns = [str(store.default_root())]
    roots: list[Path] = []
    for pattern in patterns:
        for match in sorted(glob.glob(os.path.expanduser(pattern))) or [os.path.expanduser(pattern)]:
            path = Path(match)
            if path.is_dir() and path not in roots:
                roots.append(path)
    return roots


def short(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text.startswith(home) else text


def day(timestamp: str | None) -> str:
    return (timestamp or "")[:10]


@dataclass(frozen=True)
class Place:
    """A level of the tree: a root, a memory_store in it, or a directory of that memory_store."""

    root: Path
    memory_store: str = ""
    path: str = ""


@dataclass(frozen=True)
class Entry:
    """A row of the middle table: one memory or document."""

    place: Place
    path: str
    document: bool


class Prompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, value: str = "") -> None:
        super().__init__()
        self.title_text, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text, id="dialog-title")
            yield Input(self.value, select_on_focus=False)

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class Confirm(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("enter,y", "dismiss(True)", "Yes"),
        Binding("escape,n", "dismiss(False)", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="dialog-title")
            yield Label("Enter confirms · Esc cancels", classes="quiet")


class MemoryBrowser(App[None]):
    TITLE = "memory-store"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #panes { height: 1fr; }
    #tree, #entries, #detail { border: round $surface-lighten-2; border-title-color: $text-muted; padding: 0 1; }
    #tree:focus-within, #entries:focus-within, #detail:focus-within { border: round $accent; border-title-color: $accent; }
    #tree { width: 24%; min-width: 24; }
    #entries { width: 42%; }
    #detail { width: 1fr; }
    Tree, DataTable, ListView, Markdown { background: transparent; }
    DataTable > .datatable--header { text-style: none; color: $text-muted; background: transparent; }
    DataTable { overflow-x: hidden; }
    Tree > .tree--cursor, DataTable > .datatable--cursor { background: transparent; color: $accent; text-style: bold; }
    Tree > .tree--guides, Tree > .tree--guides-hover, Tree > .tree--guides-selected { color: $surface-lighten-2; }
    #facts > ListItem { padding: 0 0 0 1; margin: 0 0 1 0; background: transparent; border-left: blank; }
    #facts > ListItem.-highlight, #facts:focus > ListItem.-highlight { background: transparent; border-left: thick $accent; }
    #detail { border-subtitle-color: $text-muted; }
    #facts > ListItem Static, #facts:focus > ListItem.-highlight Static { color: $foreground; background: transparent; text-style: none; }
    .quiet { color: $text-muted; }
    #empty { color: $text-muted; padding: 1 1; }
    #filter { display: none; border: round $accent; height: 3; }
    #filter.-open { display: block; }
    #dialog { width: 70%; max-width: 100; height: auto; padding: 1 2; border: round $accent; background: $surface; align-horizontal: center; }
    Prompt, Confirm { align: center middle; background: $background 60%; }
    #dialog-title { padding: 0 0 1 0; text-style: bold; }
    #dialog Input { border: none; background: $boost; }
    """
    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "filter", "Filter"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "add", "Add fact"),
        Binding("e", "edit", "Edit"),
        Binding("d", "retire", "Retire"),
        Binding("escape", "back", "Back", show=False),
        Binding("h,left", "focus_previous_pane", show=False),
        Binding("l,right", "focus_next_pane", show=False),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]
    PANES = ("places", "table", "facts")

    def __init__(self, roots: list[Path]) -> None:
        super().__init__()
        self.roots = roots
        self.entries: list[Entry] = []
        self.entry: Entry | None = None
        self.facts: list[dict] = []
        self.place: Place | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter memories and facts in all roots", id="filter")
        with Horizontal(id="panes"):
            with Vertical(id="tree"):
                yield Tree("roots", id="places")
            with Vertical(id="entries"):
                yield DataTable(id="table", cursor_type="row", zebra_stripes=False)
            with Vertical(id="detail"), ContentSwitcher(initial="empty", id="switch"):
                yield Static("", id="empty")
                yield ListView(id="facts")
                yield Markdown("", id="document")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        self.query_one("#tree").border_title = "memory_stores"
        self.query_one("#entries").border_title = "memories"
        self.query_one("#detail").border_title = "facts"
        table = self.query_one(DataTable)
        table.add_columns("memory", "facts", "updated", "description")
        self.build_tree()
        self.query_one(Tree).focus()

    # ---- reading

    def listing(self, place: Place) -> dict:
        try:
            return store.list_memories(place.memory_store, place.path, root=place.root)
        except (MemoryStoreError, OSError) as error:
            self.notify(str(error), severity="error")
            return {"memory_stores": [], "directories": [], "memories": [], "documents": []}

    def build_tree(self) -> None:
        tree = self.query_one(Tree)
        tree.show_root = False
        tree.clear()
        for root in self.roots:
            node = tree.root.add(short(root), data=Place(root), expand=True)
            for memory_store in self.listing(Place(root))["memory_stores"]:
                self.add_memory_store(node, root, memory_store, depth=0)
        if not self.roots:
            self.query_one("#empty", Static).update(f"No root found. List roots in {short(ROOTS_FILE)}")

    def add_memory_store(self, parent, root: Path, memory_store: str, depth: int) -> None:
        place = Place(root, memory_store)
        listing = self.listing(place)
        node = parent.add(memory_store.rsplit("/", 1)[-1], data=place, expand=depth == 0)
        self.add_directories(node, place, listing["directories"])
        for nested in listing["memory_stores"]:
            self.add_memory_store(node, root, nested, depth + 1)
        node.allow_expand = bool(node.children)

    def add_directories(self, parent, place: Place, directories: list[str]) -> None:
        for directory in directories:
            child_place = Place(place.root, place.memory_store, directory)
            child = parent.add(f"[dim]{directory.rsplit('/', 1)[-1]}/[/dim]", data=child_place)
            self.add_directories(child, child_place, self.listing(child_place)["directories"])
            child.allow_expand = bool(child.children)

    def entries_of(self, place: Place) -> list[tuple[Entry, tuple[str, str, str, str]]]:
        """The memories and documents at a place and in the directories below it."""
        if not place.memory_store:
            return []
        listing = self.listing(place)
        prefix = len(place.path) + 1 if place.path else 0
        rows = []
        for memory in listing["memories"]:
            description = "unreadable" if memory.get("error") else memory["description"]
            cells = (memory["path"][prefix:], str(memory["facts"]), day(memory["updated"]), description)
            rows.append((Entry(place, memory["path"], False), cells))
        for document in listing["documents"]:
            cells = (document["path"][prefix:], "", day(document["modified"]), "[dim]document[/dim]")
            rows.append((Entry(place, document["path"], True), cells))
        for directory in listing["directories"]:
            below = self.entries_of(Place(place.root, place.memory_store, directory))
            rows += [(entry, (entry.path[prefix:], *cells[1:])) for entry, cells in below]
        return rows

    def all_places(self) -> list[Place]:
        places: list[Place] = []

        def walk(node) -> None:
            for child in node.children:
                if child.data and child.data.memory_store and not child.data.path:
                    places.append(child.data)
                walk(child)

        walk(self.query_one(Tree).root)
        return places

    def show_entries(self, rows: list[tuple[Entry, tuple[str, str, str, str]]], title: str, keep: int = 0) -> None:
        table = self.query_one(DataTable)
        table.clear()
        self.entries = [entry for entry, _ in rows]
        for _, cells in rows:
            table.add_row(*cells)
        self.query_one("#entries").border_title = title
        if rows:
            table.move_cursor(row=min(keep, len(rows) - 1))
            self.show_entry(self.entries[table.cursor_row])
        else:
            self.show_entry(None)

    def show_place(self, place: Place, keep: int = 0) -> None:
        self.place = place
        title = "/".join(part for part in (place.memory_store, place.path) if part) or "memories"
        self.show_entries(self.entries_of(place), title, keep)

    def show_entry(self, entry: Entry | None, keep: int = 0) -> None:
        self.entry = entry
        switch = self.query_one(ContentSwitcher)
        detail = self.query_one("#detail")
        detail.border_subtitle = ""
        if entry is None:
            detail.border_title, switch.current = "facts", "empty"
            return
        detail.border_title = entry.path
        try:
            if entry.document:
                text = store.read_memories(entry.place.memory_store, [entry.path], root=entry.place.root)
                self.query_one(Markdown).update(text)
                switch.current = "document"
                return
            memory = store.read_memory(entry.place.memory_store, entry.path, root=entry.place.root)
            self.facts, detail.border_subtitle = memory["facts"], memory["description"]
        except (MemoryStoreError, OSError) as error:
            self.query_one("#empty", Static).update(str(error))
            switch.current = "empty"
            return
        facts = self.query_one("#facts", ListView)
        facts.clear()
        for fact in self.facts:
            line = f"{fact['text']}\n[dim]{fact['id']} · {day(fact['updated'])}[/dim]"
            facts.append(ListItem(Static(line)))
        if self.facts:
            index = min(keep, len(self.facts) - 1)

            def highlight() -> None:  # the items mount after this call returns
                facts.index = None
                facts.index = index

            self.call_after_refresh(highlight)
            switch.current = "facts"
        else:
            self.query_one("#empty", Static).update("no facts")
            switch.current = "empty"

    # ---- events

    @on(Tree.NodeHighlighted)
    def place_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data and not self.query_one("#filter", Input).value:
            self.show_place(event.node.data)

    @on(DataTable.RowHighlighted)
    def entry_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if 0 <= event.cursor_row < len(self.entries):
            self.show_entry(self.entries[event.cursor_row])

    @on(DataTable.RowSelected)
    def entry_selected(self) -> None:
        self.action_focus_next_pane()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            if self.place:
                self.show_place(self.place)
            return
        rows = []
        for place in self.all_places():
            for entry, cells in self.entries_of(place):
                text = f"{cells[0]} {cells[3]}"
                if not entry.document:
                    try:
                        memory = store.read_memory(place.memory_store, entry.path, root=place.root)
                        text += " " + " ".join(fact["text"] for fact in memory["facts"])
                    except (MemoryStoreError, OSError):
                        pass
                if query in text.lower():
                    rows.append((entry, (f"{place.memory_store}/{entry.path}", *cells[1:])))
        self.show_entries(rows, f"filter: {query}")

    @on(Input.Submitted, "#filter")
    def filter_submitted(self) -> None:
        self.query_one(DataTable).focus()

    # ---- actions

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.add_class("-open")
        box.focus()

    def action_back(self) -> None:
        box = self.query_one("#filter", Input)
        if box.has_class("-open"):
            box.value = ""
            box.remove_class("-open")
            self.query_one(Tree).focus()
        else:
            self.action_focus_previous_pane()

    def focused_pane(self) -> int:
        focused = self.focused.id if self.focused else ""
        return self.PANES.index(focused) if focused in self.PANES else 0

    def action_focus_next_pane(self) -> None:
        target = min(self.focused_pane() + 1, 2)
        if target == 2 and self.query_one(ContentSwitcher).current != "facts":
            return
        self.query_one(f"#{self.PANES[target]}").focus()

    def action_focus_previous_pane(self) -> None:
        self.query_one(f"#{self.PANES[max(self.focused_pane() - 1, 0)]}").focus()

    def action_cursor(self, direction: str) -> None:
        if self.focused and hasattr(self.focused, f"action_cursor_{direction}"):
            getattr(self.focused, f"action_cursor_{direction}")()

    def action_refresh(self) -> None:
        self.build_tree()

    def apply(self, op: dict) -> None:
        entry = self.entry
        if entry is None:
            return
        try:
            result = store.apply_memory_ops(entry.place.memory_store, [op], root=entry.place.root)[0]
        except (MemoryStoreError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        if not result["ok"]:
            self.notify(f"{result['error']}: {result['message']}", severity="error")
        row, fact = self.query_one(DataTable).cursor_row, self.query_one("#facts", ListView).index or 0
        if self.query_one("#filter", Input).value:
            self.show_entry(entry, fact)
        elif self.place:
            self.show_place(self.place, row)
            self.show_entry(entry, fact)

    def selected_fact(self) -> dict | None:
        index = self.query_one("#facts", ListView).index
        on_facts = self.focused_pane() == 2 and self.query_one(ContentSwitcher).current == "facts"
        return self.facts[index] if on_facts and index is not None and index < len(self.facts) else None

    def action_add(self) -> None:
        entry = self.entry
        if entry is None or entry.document:
            return

        def done(text: str | None) -> None:
            if text:
                self.apply({"op": "add", "path": entry.path, "text": text})

        self.push_screen(Prompt(f"Add fact to {entry.path}"), done)

    def action_edit(self) -> None:
        entry, fact = self.entry, self.selected_fact()
        if entry is None or entry.document:
            return
        if fact:

            def done(text: str | None) -> None:
                if text and text != fact["text"]:
                    self.apply({"op": "update", "path": entry.path, "id": fact["id"], "text": text})

            self.push_screen(Prompt(f"Edit fact {fact['id']}", fact["text"]), done)
            return
        try:
            description = store.read_memory(entry.place.memory_store, entry.path, root=entry.place.root)["description"]
        except (MemoryStoreError, OSError) as error:
            self.notify(str(error), severity="error")
            return

        def described(text: str | None) -> None:
            if text and text != description:
                self.apply({"op": "describe", "path": entry.path, "description": text})

        self.push_screen(Prompt(f"Description of {entry.path}", description), described)

    def action_retire(self) -> None:
        entry, fact = self.entry, self.selected_fact()
        if entry is None or fact is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.apply({"op": "retire", "path": entry.path, "id": fact["id"]})

        self.push_screen(Confirm(f"Retire “{fact['text'][:80]}”?"), done)


def main(arguments: list[str] | None = None) -> None:
    MemoryBrowser(load_roots(sys.argv[1:] if arguments is None else arguments)).run()


if __name__ == "__main__":
    main()
