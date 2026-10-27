import html
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")

DATA_PATH = Path(__file__).parent / "data" / "shopping_list.json"
MAX_NAME_LENGTH = 50
MIN_QTY, MAX_QTY = 1, 99
WEEKDAYS = "월화수목금토일"
DUPLICATE_MESSAGE = "같은 이름의 아이템이 이미 리스트에 있어요."
CSS_PATH = Path(__file__).parent / "style.css"
ACCENT = {"light": "#2E7D32", "dark": "#66BB6A"}  # .streamlit/config.toml의 primaryColor와 맞춘다
KST = timezone(timedelta(hours=9))


# ---------- 데이터 ----------
# JSON 파일이 유일한 저장소다. 여러 탭·사용자가 같은 파일을 쓰므로 세션에 목록을 캐시하지 않고,
# 변경은 항상 잠금 안에서 "파일 읽기 → 수정 → 쓰기"로 처리한다.

@st.cache_resource
def file_lock():
    return threading.Lock()


def clean_item(raw):
    """파일에서 읽은 아이템의 타입을 검사한다. 쓸 수 없으면 None, 수량만 이상하면 1로 바꾼다."""
    if not isinstance(raw, dict):
        return None
    if not all(isinstance(raw.get(key), str) for key in ("id", "name", "created_at")):
        return None
    if not isinstance(raw.get("checked"), bool):
        return None
    quantity = raw.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not MIN_QTY <= quantity <= MAX_QTY:
        quantity = 1
    return {
        "id": raw["id"],
        "name": raw["name"],
        "checked": raw["checked"],
        "quantity": quantity,
        "created_at": raw["created_at"],
    }


def load_items():
    """JSON 파일에서 아이템을 불러온다. 파일이 없거나 깨져 있으면 빈 리스트."""
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in map(clean_item, data) if item is not None]


def save_items(items):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DATA_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, DATA_PATH)


@contextmanager
def editing_items():
    """잠금을 잡고 최신 목록을 넘겨준 뒤, 블록이 끝나면 저장한다."""
    with file_lock():
        items = load_items()
        yield items
        save_items(items)


def find_item(items, item_id):
    return next((item for item in items if item["id"] == item_id), None)


# ---------- 규칙 ----------

def validate_name(name):
    """(통과 여부, 메시지)를 반환한다. 추가와 수정에 똑같이 사용한다."""
    name = name.strip()
    if not name:
        return False, "아이템 이름을 입력해 주세요."
    if len(name) > MAX_NAME_LENGTH:
        return False, f"아이템 이름은 {MAX_NAME_LENGTH}자 이하로 입력해 주세요."
    return True, ""


def validate_quantity(quantity):
    if not MIN_QTY <= quantity <= MAX_QTY:
        return False, f"수량은 {MIN_QTY}~{MAX_QTY} 사이로 입력해 주세요."
    return True, ""


def normalize(name):
    return "".join(name.split()).casefold()


def has_duplicate(items, name, exclude_id=None):
    """같은 이름(대소문자·공백 무시)의 미체크 아이템이 있는지 확인한다."""
    key = normalize(name)
    return any(
        not item["checked"] and item["id"] != exclude_id and normalize(item["name"]) == key
        for item in items
    )


def item_name_html(item):
    """이름은 html.escape를 거쳐 HTML 블록으로 넣는다. Markdown 서식·자동 링크가 적용되지 않는다."""
    classes = "item-name checked" if item["checked"] else "item-name"
    qty = f' <span class="item-qty">×{item["quantity"]}</span>' if item["quantity"] >= 2 else ""
    return f'<div class="{classes}"><span class="item-text">{html.escape(item["name"])}</span>{qty}</div>'


def today_text():
    """브라우저 시간대 기준 오늘 날짜. 알 수 없으면 한국 시간."""
    offset = st.context.timezone_offset  # JS getTimezoneOffset 값: UTC − 현지 시각(분)
    tz = timezone(timedelta(minutes=-offset)) if offset is not None else KST
    today = datetime.now(tz)
    return f"{today:%Y년 %m월 %d일} ({WEEKDAYS[today.weekday()]})"


# ---------- 동작 ----------

def add_item(name, quantity=1):
    for ok, message in (validate_name(name), validate_quantity(quantity)):
        if not ok:
            return False, message
    name = name.strip()
    with editing_items() as items:
        duplicate = has_duplicate(items, name)
        items.append({
            "id": str(uuid.uuid4()),
            "name": name,
            "checked": False,
            "quantity": quantity,
            "created_at": datetime.now().isoformat(timespec="microseconds"),
        })
    return True, DUPLICATE_MESSAGE if duplicate else ""


def update_item(item_id, name):
    ok, message = validate_name(name)
    if not ok:
        return False, message
    name = name.strip()
    with editing_items() as items:
        item = find_item(items, item_id)
        if item is None:  # 다른 탭에서 삭제됨: 편집만 닫는다
            return True, ""
        item["name"] = name
        duplicate = has_duplicate(items, name, exclude_id=item_id)
    return True, DUPLICATE_MESSAGE if duplicate else ""


def delete_item(item_id):
    with editing_items() as items:
        items[:] = [item for item in items if item["id"] != item_id]
    if st.session_state.get("editing_id") == item_id:
        st.session_state["editing_id"] = None


def toggle_item(item_id):
    with editing_items() as items:
        item = find_item(items, item_id)
        if item:
            item["checked"] = st.session_state[f"chk_{item_id}"]


def clear_checked():
    with editing_items() as items:
        checked_ids = {item["id"] for item in items if item["checked"]}
        items[:] = [item for item in items if not item["checked"]]
    if st.session_state.get("editing_id") in checked_ids:
        st.session_state["editing_id"] = None


# ---------- 콜백 ----------

def flash(where, level, message):
    st.session_state["flash"] = {"where": where, "level": level, "message": message}


def on_add():
    ok, message = add_item(st.session_state["new_item"], st.session_state["new_quantity"])
    if message:
        flash("add", "info" if ok else "warning", message)


def on_start_edit(item_id):
    item = find_item(load_items(), item_id)
    if item is None:
        return
    st.session_state["editing_id"] = item_id
    st.session_state[f"edit_{item_id}"] = item["name"]


def on_save_edit(item_id):
    ok, message = update_item(item_id, st.session_state[f"edit_{item_id}"])
    if ok:
        st.session_state["editing_id"] = None
    if message:
        flash(item_id, "info" if ok else "warning", message)


def on_cancel_edit():
    st.session_state["editing_id"] = None


def show_flash(where):
    if st.session_state.get("flash", {}).get("where") == where:
        notice = st.session_state.pop("flash")
        getattr(st, notice["level"])(notice["message"])


# ---------- 화면 ----------

items = load_items()
st.session_state.setdefault("editing_id", None)
if st.session_state["editing_id"] and not find_item(items, st.session_state["editing_id"]):
    st.session_state["editing_id"] = None  # 다른 탭에서 삭제된 경우

accent = ACCENT.get(st.context.theme.type, ACCENT["light"])
st.markdown(
    f"<style>:root {{ --accent: {accent}; }}\n{CSS_PATH.read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)

st.title("🛒 쇼핑 리스트")
st.caption(today_text())

with st.form("add_form", clear_on_submit=True, border=False):
    with st.container(key="add_row"):
        col_input, col_qty, col_button = st.columns([0.6, 0.2, 0.2], vertical_alignment="bottom")
        col_input.text_input("새 아이템", key="new_item", placeholder="아이템 입력...", max_chars=MAX_NAME_LENGTH)
        col_qty.number_input("수량", key="new_quantity", value=1, step=1)  # 범위는 validate_quantity에서 검사해 경고를 띄운다
        col_button.form_submit_button("추가", on_click=on_add, type="primary", width="stretch")

show_flash("add")

total = len(items)
done = sum(item["checked"] for item in items)

if total == 0:
    st.info("쇼핑 리스트가 비어 있어요. 첫 아이템을 추가해 보세요!", icon="🧺")
else:
    with st.container(border=True, key="progress_card"):
        st.markdown(f"남은 아이템 **{total - done}개** · 완료 **{done}개**")
        st.progress(done / total)

with st.container(key="item_list"):
    for item in sorted(items, key=lambda i: (i["checked"], i["created_at"])):
        item_id = item["id"]
        if st.session_state["editing_id"] == item_id:
            with st.container(border=True, key=f"item_editing_{item_id}"):
                col_edit, col_save, col_cancel = st.columns([0.6, 0.2, 0.2], vertical_alignment="center")
                col_edit.text_input(
                    "아이템 이름", key=f"edit_{item_id}", max_chars=MAX_NAME_LENGTH, label_visibility="collapsed"
                )
                col_save.button("저장", key=f"save_{item_id}", on_click=on_save_edit, args=(item_id,), type="primary")
                col_cancel.button("취소", key=f"cancel_{item_id}", on_click=on_cancel_edit)
                show_flash(item_id)
            continue

        state = "checked" if item["checked"] else "open"
        with st.container(border=True, key=f"item_{state}_{item_id}"):
            col_check, col_name, col_edit, col_delete = st.columns([0.1, 0.7, 0.1, 0.1], vertical_alignment="center")
            st.session_state[f"chk_{item_id}"] = item["checked"]  # 다른 탭에서 바뀐 값으로 위젯을 맞춘다
            col_check.checkbox(
                "구매 완료",
                key=f"chk_{item_id}",
                on_change=toggle_item,
                args=(item_id,),
                label_visibility="collapsed",
            )
            col_name.markdown(item_name_html(item), unsafe_allow_html=True)
            # 다른 아이템을 편집 중이면 수정 버튼을 막아 저장하지 않은 내용이 사라지지 않게 한다
            col_edit.button(
                "✏️", key=f"edit_btn_{item_id}", on_click=on_start_edit, args=(item_id,),
                help="수정", disabled=st.session_state["editing_id"] is not None,
            )
            col_delete.button("🗑️", key=f"del_{item_id}", on_click=delete_item, args=(item_id,), help="삭제")
            show_flash(item_id)

if total:
    st.button("체크된 항목 모두 삭제", on_click=clear_checked, disabled=done == 0, type="secondary")
