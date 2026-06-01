#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option

API = 'tgw'  # installed console script
ITEM_DATA = Path('/opt/TGW/data/ItemData')
CONFIG_PATH = Path('/opt/TGW/config/tgw-api-config.json')


def _extract_jsons(text: str):
    decoder = json.JSONDecoder()
    idx = 0
    out = []
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        out.append(obj)
        idx = end
    return out


def api(*args):
    p = subprocess.run([API, *args], capture_output=True, text=True)
    payloads = _extract_jsons(p.stdout or '')
    if payloads:
        first = payloads[0]
        if isinstance(first, list):
            return first
        if isinstance(first, dict):
            if first.get('ok') is False:
                raise RuntimeError(first.get('error') or p.stderr.strip() or 'api failed')
            if 'items' in first and isinstance(first['items'], list):
                return first['items']
            if 'rows' in first and isinstance(first['rows'], list):
                return first['rows']
            return first
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'api failed')
    return json.loads(p.stdout)


def load_runtime_config():
    default = {'json_editor': 'jsonedit', 'image_viewer': 'gwenview'}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            default.update(cfg)
    except Exception:
        pass
    return default


class DetailPane(Static, can_focus=True):
    pass


class SortMenu(ModalScreen[str | None]):
    CSS = """
    SortMenu { align: center middle; }
    #sort_box { width: 40; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    #sort_title { margin-bottom: 1; text-style: bold; }
    #sort_options { height: auto; max-height: 10; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id='sort_box'):
            yield Static('Sort by', id='sort_title')
            yield OptionList(
                Option('SKU', id='sku'),
                Option('Title', id='title'),
                Option('Location', id='location'),
                Option('Status', id='status'),
                Option('Category', id='category'),
                id='sort_options',
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option_id) if event.option_id else None)

    def key_escape(self) -> None:
        self.dismiss(None)


class Browser(App):
    CSS = """
    Screen { layout: horizontal; }
    #left { width: 1fr; min-width: 36; border: solid gray; }
    #right { width: 39; min-width: 28; max-width: 39; border: solid gray; }
    #search { dock: top; margin: 0 0 1 0; }
    #tree, #result_table, #detail_scroll { height: 1fr; }
    .hidden { display: none; }
    #detail_scroll { overflow-y: auto; overflow-x: auto; }
    #detail { height: auto; min-height: 100%; padding: 0 1; }
    """

    search_term = reactive('')
    current_sku = reactive('')
    sort_column = reactive('sku')
    sort_reverse = reactive(False)
    browse_mode = reactive('list')

    BINDINGS = [
        ('f2', 'sort_menu', 'Sort Menu'),
        ('f3', 'toggle_browse', 'Tree/List'),
        ('f4', 'toggle_expand_all', 'Expand All'),
        ('f6', 'sort_sku', 'Sort SKU'),
        ('f7', 'sort_title', 'Sort Title'),
        ('f8', 'sort_location', 'Sort Location'),
        ('f9', 'sort_status', 'Sort Status'),
        ('f10', 'sort_category', 'Sort Category'),
    ]

    def __init__(self, startup_search=''):
        super().__init__()
        self.startup_search = startup_search.strip()
        self.filtered_rows = []
        self.tree_locations = {}
        self.tree_status = {}
        self.tree_category = {}
        self.tree_skus = {}
        self.row_order = []
        self.suppress_tree_select = False
        self.runtime_config = load_runtime_config()
        self.expand_all_tree = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id='left'):
                yield Input(placeholder='Search sku/title/location/status', id='search')
                yield Tree('Browse', id='tree')
                yield DataTable(id='result_table')
            with Vertical(id='right'):
                with VerticalScroll(id='detail_scroll'):
                    yield DetailPane('Select an item', id='detail')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#result_table', DataTable)
        table.cursor_type = 'row'
        table.zebra_stripes = True
        table.add_columns('SKU', 'Title', 'Location', 'Status', 'Category')
        self.query_one('#tree', Tree).auto_expand = False
        search_box = self.query_one('#search', Input)
        if self.startup_search:
            search_box.value = self.startup_search
            self.search_term = self.startup_search
        self.load_results(self.search_term)
        self.set_browse_mode('list')
        search_box.focus()

    def normalize_title(self, row):
        return str(row.get('title', row.get('Title', '')))

    def normalize_status(self, row):
        return str(row.get('#STATUS', row.get('status', '')))

    def normalize_category(self, row):
        return str(row.get('Category ID', row.get('ebaycat', row.get('category', ''))))

    def escape_markup(self, value):
        text = str(value)
        return text.replace('[', r'\\[').replace(']', r'\\]')

    def set_browse_mode(self, mode):
        self.browse_mode = mode
        tree = self.query_one('#tree', Tree)
        table = self.query_one('#result_table', DataTable)
        if mode == 'tree':
            tree.remove_class('hidden')
            table.add_class('hidden')
        else:
            table.remove_class('hidden')
            tree.add_class('hidden')

    def sort_rows(self, rows):
        col = self.sort_column
        rev = self.sort_reverse
        def keyfunc(row):
            def keyfunc(row):
            if col == 'sku':
                return str(row.get('sku', '')).lower()
            if col == 'title':
                return self.normalize_title(row).lower()
            if col == 'location':
                return str(row.get('location', '')).lower()
            if col == 'status':
                return self.normalize_status(row).lower()
            if col == 'category':
                return self.normalize_category(row).lower()
            return str(row).lower()
            return str(row).lower()
        return sorted(rows, key=keyfunc, reverse=rev)

    def add_row_leaf_details(self, sku_node, row):
        for key in sorted(row.keys(), key=str.lower):
            value = row.get(key, '')
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            value = str(value)
            sku_node.add_leaf(f'{key}: {value[:120]}', data={'kind': 'field', 'sku': str(row.get('sku', '')), 'key': key, 'value': value})

    def expand_tree_for_search_state(self):
        tree = self.query_one('#tree', Tree)
        has_search = bool(str(self.search_term).strip())
        for node in tree.root.children:
            label = str(node.label)
            if self.expand_all_tree or has_search:
                node.expand()
                for child in node.children:
                    child.expand()
            else:
                for child in node.children:
                    child.collapse()
                if label == 'Locations':
                    node.expand()
                else:
                    node.collapse()
        tree.root.expand()

    def build_tree_from_rows(self, rows):
        tree = self.query_one('#tree', Tree)
        tree.root.remove_children()
        tree.root.set_label('Browse')
        tree.root.expand()
        self.tree_locations = {}
        self.tree_status = {}
        self.tree_category = {}
        self.tree_skus = {}
        by_loc, by_status, by_cat = {}, {}, {}
        for row in rows:
            loc = str(row.get('location', 'unknown') or 'unknown')
            status = self.normalize_status(row) or 'unknown'
            category = self.normalize_category(row) or 'uncategorized'
            by_loc.setdefault(loc, []).append(row)
            by_status.setdefault(status, []).append(row)
            by_cat.setdefault(category, []).append(row)
        top_loc = tree.root.add('Locations', data={'kind': 'branch', 'value': 'locations'})
        top_status = tree.root.add('Status', data={'kind': 'branch', 'value': 'status'})
        top_cat = tree.root.add('Category', data={'kind': 'branch', 'value': 'category'})
        for loc in sorted(by_loc, key=str.lower):
            loc_rows = sorted(by_loc[loc], key=lambda r: str(r.get('sku', '')))
            node = top_loc.add(f'{loc} ({len(loc_rows)})', data={'kind': 'location', 'value': loc})
            self.tree_locations[loc] = node
            for row in loc_rows:
                sku = str(row.get('sku', ''))
                title = self.normalize_title(row)[:60]
                sku_node = node.add(f'{sku} — {title}', data={'kind': 'sku', 'value': sku})
                self.tree_skus[sku] = sku_node
                self.add_row_leaf_details(sku_node, row)
        for status in sorted(by_status, key=str.lower):
            status_rows = sorted(by_status[status], key=lambda r: str(r.get('sku', '')))
            node = top_status.add(f'{status} ({len(status_rows)})', data={'kind': 'status', 'value': status})
            self.tree_status[status] = node
            for row in status_rows:
                sku = str(row.get('sku', ''))
                title = self.normalize_title(row)[:60]
                node.add_leaf(f'{sku} — {title}', data={'kind': 'sku', 'value': sku})
        for category in sorted(by_cat, key=str.lower):
            cat_rows = sorted(by_cat[category], key=lambda r: str(r.get('sku', '')))
            node = top_cat.add(f'{category} ({len(cat_rows)})', data={'kind': 'category', 'value': category})
            self.tree_category[category] = node
            for row in cat_rows:
                sku = str(row.get('sku', ''))
                title = self.normalize_title(row)[:60]
                node.add_leaf(f'{sku} — {title}', data={'kind': 'sku', 'value': sku})
        self.expand_tree_for_search_state()

    def load_results(self, search=''):
        table = self.query_one('#result_table', DataTable)
        table.clear()
        rows = api('list', '--search', search) if search else api('list')
        rows = self.sort_rows(rows)
        self.filtered_rows = rows
        self.row_order = []
        for row in rows:
            sku = str(row.get('sku', ''))
            title = self.normalize_title(row)
            loc = str(row.get('location', ''))
            status = self.normalize_status(row)
            category = self.normalize_category(row)
            table.add_row(sku, title[:60], loc, status, category, key=sku)
            self.row_order.append(sku)
        self.build_tree_from_rows(rows)

    def reveal_item_in_tree(self, sku, location_value=None):
        tree = self.query_one('#tree', Tree)
        sku_node = self.tree_skus.get(str(sku))
        if sku_node is not None:
            try:
                if sku_node.parent is not None and sku_node.parent.parent is not None:
                    sku_node.parent.parent.expand()
                if sku_node.parent is not None:
                    sku_node.parent.expand()
                sku_node.expand()
                self.suppress_tree_select = True
                tree.select_node(sku_node)
                try:
                    region = sku_node.region
                    if region is not None:
                        tree.scroll_to_region(region, spacing=(1, 0, 8, 0), animate=False)
                except Exception:
                    pass
                return
            except Exception:
                pass
            finally:
                self.suppress_tree_select = False
        node = self.tree_locations.get(str(location_value))
        if node is not None:
            try:
                if node.parent is not None:
                    node.parent.expand()
                node.expand()
            except Exception:
                pass

    def show_item(self, sku, reveal_location=False):
        if self.current_sku == sku and not reveal_location:
            return
        self.current_sku = sku
        item = api('get', sku)
        sku_value = self.escape_markup(item.get('sku', sku))
        title_value = self.escape_markup(item.get('title', item.get('Title', '')))
        body = self.escape_markup(json.dumps(item, ensure_ascii=False, indent=2))
        text = f'SKU: [b]{sku_value}[/b]\nTITLE: [b]{title_value}[/b]\n{"=" * 60}\n{body}'
        self.query_one('#detail', DetailPane).update(text)
        self.query_one('#detail_scroll', VerticalScroll).scroll_home(animate=False)
        if reveal_location:
            self.reveal_item_in_tree(item.get('sku', sku), item.get('location', 'unknown'))

    def action_toggle_browse(self):
        self.set_browse_mode('tree' if self.browse_mode == 'list' else 'list')

    def action_toggle_expand_all(self):
        self.expand_all_tree = not self.expand_all_tree
        self.expand_tree_for_search_state()

    def action_sort_menu(self):
        def apply_sort(choice):
            if choice:
                self.toggle_sort(choice)
        self.push_screen(SortMenu(), apply_sort)

    def action_sort_sku(self): self.toggle_sort('sku')
    def action_sort_title(self): self.toggle_sort('title')
    def action_sort_location(self): self.toggle_sort('location')
    def action_sort_status(self): self.toggle_sort('status')
    def action_sort_category(self): self.toggle_sort('category')

    def toggle_sort(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.load_results(self.search_term)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == 'search':
            self.search_term = event.value.strip()
            self.load_results(self.search_term)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        sku = str(event.row_key.value)
        if sku:
            self.show_item(sku, reveal_location=True)

    def on_data_table_header_selected(self, event):
        mapping = {'SKU': 'sku', 'Title': 'title', 'Location': 'location', 'Status': 'status', 'Category': 'category'}
        column = mapping.get(str(event.label))
        if column:
            self.toggle_sort(column)

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        if self.suppress_tree_select:
            return
        data = event.node.data or {}
        kind = data.get('kind')
        if kind == 'sku':
            self.show_item(data.get('value', ''), reveal_location=False)
        elif kind == 'field':
            sku = data.get('sku', '')
            if sku:
                self.current_sku = sku

    def on_tree_node_expanded(self, event: Tree.NodeExpanded):
        data = event.node.data or {}
        if data.get('kind') == 'sku':
            self.current_sku = data.get('value', '')

    def on_click(self, event: events.Click) -> None:
        if event.chain != 2:
            return
        detail = self.query_one('#detail', DetailPane)
        if event.widget is not detail:
            return
        sku = str(self.current_sku or '').strip()
        if not sku:
            return
        sku_dir = ITEM_DATA / sku
        json_path = sku_dir / f'{sku}.json'
        if event.button == 1:
            if not json_path.exists():
                return
            editor = str(self.runtime_config.get('json_editor', 'jsonedit')).strip() or 'jsonedit'
            try:
                subprocess.Popen([editor, str(json_path)])
            except Exception:
                pass
        elif event.button == 3:
            if not sku_dir.exists():
                return
            viewer = str(self.runtime_config.get('image_viewer', 'gwenview')).strip() or 'gwenview'
            try:
                subprocess.Popen([viewer, str(sku_dir)])
            except Exception:
                pass

def main():
    startup_search = ' '.join(sys.argv[1:]).strip()
    Browser(startup_search=startup_search).run()


if __name__ == '__main__':
    main()
