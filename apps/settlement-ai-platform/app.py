"""Settlement AI Workbench MVP — FastAPI application.

Run:
  pip install -r requirements.txt
  python -m uvicorn app:app --port 8090
Demo login (PLATFORM_DEMO=1, default): append ?as=ziliao / jiesuan / wuzi / jingli / admin
Normal login: admin/admin123, ziliao/123456, jiesuan/123456, wuzi/123456, jingli/123456
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from itsdangerous import BadSignature, URLSafeSerializer

from parsers import parse_file, parse_material_issue_xlsx
from seed import check_pw, connect, init_db, now

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", str(BASE / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.db"
SECRET = os.environ.get("PLATFORM_SECRET", "settlement-mvp-demo-secret")
DEMO = os.environ.get("PLATFORM_DEMO", "1") == "1"
serializer = URLSafeSerializer(SECRET, salt="platform-session")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
init_db(str(DB_PATH))

app = FastAPI(title="结算AI工作台 MVP")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def money(v):
    try:
        return f"{float(v or 0):,.0f}"
    except Exception:
        return str(v)


templates.env.filters["money"] = money


def get_db():
    conn = connect(str(DB_PATH))
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn=Depends(get_db)):
    raw = request.cookies.get("session")
    uid = None
    if raw:
        try:
            uid = serializer.loads(raw, max_age=86400 * 7)
        except BadSignature:
            uid = None
    if uid:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            return row
    if DEMO:
        username = request.query_params.get("as")
        if username:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if row:
                return row
    return None


def require_user(user):
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def project_ids(user, conn):
    if user["role"] in ("admin", "manager"):
        return [r["id"] for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    rows = conn.execute(
        "SELECT p.id FROM projects p JOIN members m ON m.project_id=p.id WHERE m.user_id=? ORDER BY p.id",
        (user["id"],),
    ).fetchall()
    return [r["id"] for r in rows]


def get_project(conn, pid, user):
    ids = project_ids(user, conn)
    if pid not in ids:
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def audit(conn, pid, user, action, detail=""):
    conn.execute(
        "INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (pid, user["id"] if user else None, action, detail, now()),
    )
    conn.commit()


def doc_counts(conn, pid):
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM documents WHERE project_id=? GROUP BY status", (pid,)
    ).fetchall()
    d = {r["status"]: r["c"] for r in rows}
    d["total"] = sum(d.values())
    return d


def upsert_profile(conn, pid, key, value):
    if value is None or value == "":
        return
    conn.execute("INSERT OR REPLACE INTO profiles (project_id, key, value) VALUES (?,?,?)", (pid, key, str(value)))


def apply_parse(conn, pid, doc_name, category, parsed):
    data = parsed.get("data") or {}
    changed = {}
    mats = data.get("materials") or []
    if mats:
        conn.execute("DELETE FROM materials WHERE project_id=?", (pid,))
        for m in mats:
            conn.execute(
                "INSERT INTO materials (project_id, supply, code, name, spec, unit, qty, source) VALUES (?,?,?,?,?,?,?,?)",
                (pid, m.get("supply"), m.get("code"), m.get("name"), m.get("spec"), m.get("unit"), m.get("qty"), f"导入：{doc_name}"),
            )
        changed["materials"] = len(mats)
        a = sum(1 for m in mats if m.get("supply") == "甲供")
        b = sum(1 for m in mats if m.get("supply") == "乙供")
        upsert_profile(conn, pid, "甲供材料", f"{a} 条")
        upsert_profile(conn, pid, "乙供材料", f"{b} 条")
    boq = data.get("boq") or []
    if boq:
        conn.execute("DELETE FROM boq WHERE project_id=?", (pid,))
        for b in boq:
            conn.execute(
                "INSERT INTO boq (project_id, code, name, unit, qty, part) VALUES (?,?,?,?,?,?)",
                (pid, b.get("code"), b.get("name"), b.get("unit"), b.get("qty"), b.get("part")),
            )
        changed["boq"] = len(boq)
        upsert_profile(conn, pid, "工程量清单", f"{len(boq)} 条")
    budget = data.get("budget_items") or []
    summary = data.get("budget_summary")
    if budget:
        conn.execute("DELETE FROM budget_items WHERE project_id=?", (pid,))
        for b in budget:
            conn.execute("INSERT INTO budget_items (project_id, name, amount, category) VALUES (?,?,?,?)", (pid, b.get("name"), b.get("amount"), b.get("category")))
        total = sum(float(b.get("amount") or 0) for b in budget)
        conn.execute("UPDATE projects SET budget_total=? WHERE id=?", (total, pid))
        upsert_profile(conn, pid, "预算", f"{total/10000:.2f} 万元（导入：{doc_name}）")
        changed["budget"] = total
    profile = data.get("profile") or {}
    for k, v in profile.items():
        if k in ("项目名称",) and not v:
            continue
        upsert_profile(conn, pid, k, v)
    if category == "设计说明书" or doc_name.lower().endswith(".docx"):
        upsert_profile(conn, pid, "设计说明", f"{doc_name} 已解析")
    if changed:
        upsert_profile(conn, pid, "最近导入", f"{doc_name}：材料{changed.get('materials',0)}条、工程量{changed.get('boq',0)}条、预算{changed.get('budget',0):.0f}元" if changed.get('budget') else f"{doc_name}：材料{changed.get('materials',0)}条、工程量{changed.get('boq',0)}条")
    conn.commit()
    return changed


def can_manage(user):
    return user and user["role"] in ("admin", "manager")


def material_issue_rows(conn, station="", team="", status=""):
    sql = "SELECT * FROM material_issues WHERE 1=1"
    args = []
    if station:
        sql += " AND station=?"; args.append(station)
    if team:
        sql += " AND team=?"; args.append(team)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY station, team, id"
    rows = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["diff"] = max(float(d.get("issued_qty") or 0) - float(d.get("settled_qty") or 0), 0)
        d["amount"] = d["diff"] * float(d.get("price") or 0)
        d["recovered_amount"] = min(float(d.get("recovered_qty") or 0), d["diff"]) * float(d.get("price") or 0)
        if d["diff"] > 0:
            rows.append(d)
    return rows


def group_issues(rows, key):
    groups = {}
    for r in rows:
        k = r[key]
        g = groups.setdefault(k, {"name": k, "items": 0, "teams": set(), "stations": set(), "amount": 0.0, "pending": 0.0, "recovered": 0.0})
        g["items"] += 1
        g["teams"].add(r["team"])
        g["stations"].add(r["station"])
        g["amount"] += r["amount"]
        g["recovered"] += r["recovered_amount"]
        if r["status"] != "已追回":
            g["pending"] += r["amount"] - r["recovered_amount"]
    out = []
    for g in groups.values():
        g["team_count"] = len(g.pop("teams"))
        g["station_names"] = "、".join(sorted(g.pop("stations")))
        g["rate"] = (g["recovered"] / g["amount"] * 100) if g["amount"] else 0
        out.append(g)
    out.sort(key=lambda x: -x["amount"])
    return out


def generate_draft(conn, project, scene):
    profile = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM profiles WHERE project_id=?", (project["id"],))}
    docs = conn.execute("SELECT name, status, summary FROM documents WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
    compares = conn.execute("SELECT * FROM compare_items WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
    pending_docs = [d["name"] for d in docs if d["status"] not in ("parsed",)]
    diff = [c for c in compares if c["status"] != "通过"]
    prompt = (
        f"项目：{project['name']}（{project['code']}），阶段：{project['stage']}，投产日：{project['t0']}。\n"
        f"项目画像：{json.dumps(profile, ensure_ascii=False)}\n"
        f"资料状态：{[(d['name'], d['status']) for d in docs]}\n"
        f"三算差异：{[(c['item'], c['design_qty'], c['budget_qty'], c['settle_qty'], c['reason']) for c in compares]}"
    )
    engine = "规则初稿"
    output = ""
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    if base_url and api_key:
        try:
            import httpx

            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": os.environ.get("LLM_MODEL", "default"),
                    "messages": [
                        {"role": "system", "content": "你是配电网结算资料审查助手。只使用用户提供的项目数据，不臆造数量或条款；输出中文，分条列明。"},
                        {"role": "user", "content": prompt + f"\n请按场景“{scene}”生成初稿。"},
                    ],
                    "temperature": 0.2,
                },
                timeout=30,
            )
            resp.raise_for_status()
            output = resp.json()["choices"][0]["message"]["content"].strip()
            engine = "私有化LLM"
        except Exception as e:
            output = ""
            engine = f"规则初稿（LLM调用失败：{e}）"
    if not output:
        lines = [f"【{scene}】{project['name']}（{project['code']}）", ""]
        if scene == "资料完整性检查":
            lines.append(f"资料共 {len(docs)} 项，已解析 {len(docs)-len(pending_docs)} 项，待处理/需插件 {len(pending_docs)} 项。")
            if pending_docs:
                lines.append("待处理：" + "、".join(pending_docs))
            lines.append("建议：先补齐附件3/附件5的插件解析，再提交复核。")
        elif scene == "设计资料解析":
            lines.append("项目画像：" + "；".join(f"{k}={v}" for k, v in profile.items()))
            lines.append(f"预算总额：{money(project['budget_total'])} 元。")
            lines.append("建议：核对项目编码、建设规模、材料清册与预算口径。")
        elif scene == "三算对比":
            lines.append(f"共 {len(compares)} 项对比，其中 {len(diff)} 项需人工确认。")
            for c in diff:
                lines.append(f"- {c['item']}：设计 {c['design_qty']}，预算 {c['budget_qty']}，结算 {c['settle_qty']}；原因：{c['reason']}；证据：{c['evidence']}")
            lines.append("说明：AI只列差异，调整由人按依据确认。")
        else:
            lines.append("已读取项目资料与对比数据，请按操作卡逐项复核。")
        output = "\n".join(lines)
    return prompt, output, engine


@app.get("/health")
def health():
    return {"ok": True, "app": "settlement-ai-platform", "demo": DEMO}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"msg": request.query_params.get("msg", "")})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not check_pw(password, row["password"]):
        return RedirectResponse("/login?msg=用户名或密码错误", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", serializer.dumps(row["id"]), httponly=True, samesite="lax", max_age=86400 * 7)
    audit(conn, None, row, "login", "登录")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    ids = project_ids(user, conn)
    projects = conn.execute(
        "SELECT * FROM projects WHERE id IN (%s) ORDER BY id" % ",".join("?" * len(ids)),
        ids,
    ).fetchall() if ids else []
    alerts = conn.execute(
        "SELECT a.*, p.name pname FROM alerts a JOIN projects p ON p.id=a.project_id WHERE a.project_id IN (%s) AND a.status='待处理' ORDER BY CASE a.level WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END"
        % ",".join("?" * len(ids)),
        ids,
    ).fetchall() if ids else []
    tasks = conn.execute(
        "SELECT t.*, p.name pname FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.project_id IN (%s) AND t.status!='完成' ORDER BY t.due"
        % ",".join("?" * len(ids)),
        ids,
    ).fetchall() if ids else []
    stats = {
        "projects": len(projects),
        "docs_pending": sum(doc_counts(conn, p["id"]).get("pending", 0) + doc_counts(conn, p["id"]).get("needs_plugin", 0) for p in projects),
        "risk": len(alerts),
        "drafts": conn.execute("SELECT COUNT(*) c FROM ai_outputs WHERE status='初稿'").fetchone()["c"],
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"user": user, "projects": projects, "alerts": alerts, "tasks": tasks, "stats": stats, "active": "dashboard", "can_manage": can_manage(user)},
    )


@app.get("/material-issues", response_class=HTMLResponse)
def material_issues_page(
    request: Request, station: str = "", team: str = "", status: str = "",
    user=Depends(current_user), conn=Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    rows = material_issue_rows(conn, station, team, status)
    all_rows = material_issue_rows(conn)
    by_station = group_issues(rows, "station")
    by_team = group_issues(rows, "team")
    total_amount = sum(r["amount"] for r in rows)
    total_pending = sum(r["amount"] - r["recovered_amount"] for r in rows if r["status"] != "已追回")
    total_recovered = sum(r["recovered_amount"] for r in rows)
    mat_totals = {}
    for r in rows:
        mat_totals.setdefault(r["material_name"], 0)
        mat_totals[r["material_name"]] += r["amount"]
    top_material = max(mat_totals.items(), key=lambda x: x[1])[0] if mat_totals else "—"
    top_station = by_station[0]["name"] if by_station else "—"
    top_team = by_team[0]["name"] if by_team else "—"
    summary = (
        f"共发现出库大于结算的物料 {len(rows)} 条，超领金额 {total_amount:,.0f} 元，待追回 {total_pending:,.0f} 元，已追回 {total_recovered:,.0f} 元。"
        f"超领金额最高的供电所是{top_station}，班组是{top_team}，材料是{top_material}。"
        "建议先向金额最高的班组发追回通知，再按供电所汇总与物资系统出库单逐条核对。"
    )
    stations = [r["station"] for r in conn.execute("SELECT DISTINCT station FROM material_issues ORDER BY station")]
    teams = [r["team"] for r in conn.execute("SELECT DISTINCT team FROM material_issues ORDER BY team")]
    statuses = [r["status"] for r in conn.execute("SELECT DISTINCT status FROM material_issues ORDER BY status")]
    return templates.TemplateResponse(
        request, "material_issues.html",
        {"user": user, "rows": rows, "all_count": len(all_rows), "by_station": by_station, "by_team": by_team,
         "station_options": stations, "team_options": teams, "status_options": statuses,
         "station": station, "team": team, "status": status, "summary": summary,
         "total_amount": total_amount, "total_pending": total_pending, "total_recovered": total_recovered,
         "active": "material_issues"},
    )


@app.get("/material-issues/template")
def material_issue_template(request: Request, user=Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    wb = Workbook(); ws = wb.active; ws.title = "材料超领导入"
    headers = ["供电所", "班组", "项目", "材料编码", "材料名称", "规格型号", "单位", "出库数量", "结算数量", "单价", "已追回数量", "状态", "责任人", "备注"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8F0FE")
    ws.append(["供电所A", "班组甲", "项目A·某台区改造工程", "020200300010023", "1kV铝芯交联聚乙烯绝缘架空电缆", "JKLYJ-120(1kV)", "米", 1200, 1100, 12, 0, "待追回", "物资员", "演示行，可删除"])
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[chr(64 + i)].width = max(10, min(22, len(h) * 2 + 6))
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=material-issue-template.xlsx"})


@app.post("/material-issues/import")
def material_issue_import(request: Request, file: UploadFile = File(...), user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = file.file.read()
    dest = UPLOAD_DIR / f"material-issue-{uuid.uuid4().hex[:8]}.xlsx"
    dest.write_bytes(raw)
    rows = parse_material_issue_xlsx(str(dest))
    if not rows:
        return RedirectResponse("/material-issues?msg=未识别到有效数据，请按模板填写", status_code=303)
    conn.execute("DELETE FROM material_issues")
    for r in rows:
        conn.execute(
            "INSERT INTO material_issues (station, team, project_name, material_code, material_name, spec, unit, issued_qty, settled_qty, price, recovered_qty, status, responsible, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["station"], r["team"], r["project_name"], r["material_code"], r["material_name"], r["spec"], r["unit"], r["issued_qty"], r["settled_qty"], r["price"], r["recovered_qty"], r["status"], r["responsible"], r["note"]),
        )
    conn.commit()
    audit(conn, None, user, "material_import", f"导入材料超领 {len(rows)} 条")
    return RedirectResponse(f"/material-issues?msg=已导入 {len(rows)} 条材料超领记录", status_code=303)


@app.post("/material-issues/{iid}/recover")
def material_issue_recover(iid: int, request: Request, recovered_qty: float = Form(0), status: str = Form("追回中"), responsible: str = Form(""), user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    row = conn.execute("SELECT * FROM material_issues WHERE id=?", (iid,)).fetchone()
    if not row:
        raise HTTPException(404)
    diff = max(float(row["issued_qty"] or 0) - float(row["settled_qty"] or 0), 0)
    qty = min(max(recovered_qty, 0), diff)
    if status == "已追回":
        qty = diff
    conn.execute("UPDATE material_issues SET recovered_qty=?, status=?, responsible=? WHERE id=?", (qty, status, responsible or row["responsible"], iid))
    conn.commit()
    audit(conn, None, user, "material_recover", f"材料超领 #{iid} 追回 {qty}")
    return RedirectResponse("/material-issues?msg=追回进度已更新", status_code=303)


@app.get("/material-issues/export")
def material_issue_export(request: Request, station: str = "", team: str = "", status: str = "", user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    rows = material_issue_rows(conn, station, team, status)
    wb = Workbook(); ws = wb.active; ws.title = "材料超领分析"
    headers = ["供电所", "班组", "项目", "材料编码", "材料名称", "规格型号", "单位", "出库数量", "结算数量", "差异数量", "单价", "超领金额", "已追回数量", "已追回金额", "状态", "责任人", "备注"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="E8F0FE")
    for r in rows:
        ws.append([r["station"], r["team"], r["project_name"], r["material_code"], r["material_name"], r["spec"], r["unit"], r["issued_qty"], r["settled_qty"], r["diff"], r["price"], r["amount"], r["recovered_qty"], r["recovered_amount"], r["status"], r["responsible"], r["note"]])
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[chr(64 + i)].width = max(10, min(24, len(h) * 2 + 6))
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=material-issues.xlsx"})


@app.get("/projects/new", response_class=HTMLResponse)
def project_new(request: Request, user=Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can_manage(user):
        raise HTTPException(403, "只有管理员/项目经理可以新建项目")
    return templates.TemplateResponse(request, "project_form.html", {"user": user, "mode": "new", "p": None, "active": "project"})


@app.post("/projects")
def project_create(
    request: Request, code: str = Form(...), name: str = Form(...), ptype: str = Form("台区改造"),
    stage: str = Form("资料解析"), t0: str = Form(""), owner: str = Form(""), location: str = Form(""),
    note: str = Form(""), user=Depends(current_user), conn=Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can_manage(user):
        raise HTTPException(403, "只有管理员/项目经理可以新建项目")
    try:
        cur = conn.execute(
            "INSERT INTO projects (code, name, ptype, stage, t0, owner, location, budget_total, note) VALUES (?,?,?,?,?,?,?,0,?)",
            (code.strip(), name.strip(), ptype, stage, t0, owner or user["name"], location, note),
        )
        pid = cur.lastrowid
        conn.execute("INSERT INTO members (user_id, project_id) VALUES (?,?)", (user["id"], pid))
        conn.commit()
        audit(conn, pid, user, "create_project", f"{code} {name}")
        return RedirectResponse(f"/projects/{pid}?tab=docs&msg=项目已创建，请上传设计资料包", status_code=303)
    except sqlite3.IntegrityError:
        return RedirectResponse("/projects/new?msg=项目编码已存在", status_code=303)


@app.get("/projects/{pid}/edit", response_class=HTMLResponse)
def project_edit_page(pid: int, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can_manage(user):
        raise HTTPException(403, "只有管理员/项目经理可以编辑项目")
    project = get_project(conn, pid, user)
    return templates.TemplateResponse(request, "project_form.html", {"user": user, "mode": "edit", "p": project, "active": "project"})


@app.post("/projects/{pid}/edit")
def project_edit(
    pid: int, request: Request, code: str = Form(...), name: str = Form(...), ptype: str = Form(...),
    stage: str = Form(...), t0: str = Form(""), owner: str = Form(""), location: str = Form(""),
    note: str = Form(""), user=Depends(current_user), conn=Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not can_manage(user):
        raise HTTPException(403, "只有管理员/项目经理可以编辑项目")
    project = get_project(conn, pid, user)
    try:
        conn.execute(
            "UPDATE projects SET code=?, name=?, ptype=?, stage=?, t0=?, owner=?, location=?, note=? WHERE id=?",
            (code.strip(), name.strip(), ptype, stage, t0, owner or project["owner"], location, note, pid),
        )
        conn.commit()
        audit(conn, pid, user, "edit_project", f"{code} {name}")
        return RedirectResponse(f"/projects/{pid}?tab=overview&msg=项目信息已更新", status_code=303)
    except sqlite3.IntegrityError:
        return RedirectResponse(f"/projects/{pid}/edit?msg=项目编码已存在", status_code=303)


@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_page(pid: int, request: Request, tab: str = "overview", user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    project = get_project(conn, pid, user)
    profile = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM profiles WHERE project_id=?", (pid,))}
    docs = conn.execute("SELECT * FROM documents WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    materials = conn.execute("SELECT * FROM materials WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    boq = conn.execute("SELECT * FROM boq WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    budget = conn.execute("SELECT * FROM budget_items WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    compares = conn.execute("SELECT * FROM compare_items WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    alerts = conn.execute("SELECT * FROM alerts WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    ai_outputs = conn.execute("SELECT * FROM ai_outputs WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()
    reviews = conn.execute("SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()
    return templates.TemplateResponse(
        request, "project.html",
        {
            "user": user, "p": project, "tab": tab, "profile": profile,
            "docs": docs, "materials": materials, "boq": boq, "budget": budget,
            "compares": compares, "tasks": tasks, "alerts": alerts, "ai_outputs": ai_outputs,
            "reviews": reviews, "doccounts": doc_counts(conn, pid), "active": "project", "can_manage": can_manage(user),
        },
    )


@app.post("/projects/{pid}/documents")
def upload_document(
    pid: int, request: Request, file: list[UploadFile] = File(...), name: str = Form(""), category: str = Form("其他"),
    user=Depends(current_user), conn=Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    get_project(conn, pid, user)
    results = []
    for f in file:
        raw = f.file.read()
        safe = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", f.filename or "upload")
        dest_dir = UPLOAD_DIR / f"project_{pid}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{safe}"
        dest.write_bytes(raw)
        parsed = parse_file(str(dest), f.filename)
        doc_name = (name if len(file) == 1 and name else f.filename) or safe
        cur = conn.execute(
            "INSERT INTO documents (project_id, name, category, filename, path, size, status, summary, parsed_json, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, doc_name, category, f.filename, str(dest), len(raw), parsed["status"], parsed["summary"], json.dumps(parsed, ensure_ascii=False), user["id"], now()),
        )
        conn.commit()
        changed = apply_parse(conn, pid, doc_name, category, parsed)
        audit(conn, pid, user, "upload", f"上传 {f.filename}，状态 {parsed['status']}，导入 {changed}")
        results.append((parsed["status"], changed))
    ok = sum(1 for r in results if r[0] == "parsed")
    plugin = sum(1 for r in results if r[0] == "needs_plugin")
    err = sum(1 for r in results if r[0] == "error")
    msg = f"上传 {len(file)} 个文件：已解析 {ok}，需插件 {plugin}，失败 {err}"
    if any(r[1] for r in results):
        msg += "；结构化数据已导入"
    return RedirectResponse(f"/projects/{pid}?tab=docs&msg={msg}", status_code=303)


@app.post("/documents/{did}/parse")
def reparse_document(did: int, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not doc:
        raise HTTPException(404)
    project = get_project(conn, doc["project_id"], user)
    if not doc["path"] or not Path(doc["path"]).exists():
        parsed = {"status": "pending", "summary": "演示数据无原始文件，请上传文件后解析", "data": {}, "text": ""}
    else:
        parsed = parse_file(doc["path"], doc["filename"])
    conn.execute("UPDATE documents SET status=?, summary=?, parsed_json=? WHERE id=?", (parsed["status"], parsed["summary"], json.dumps(parsed, ensure_ascii=False), did))
    conn.commit()
    audit(conn, doc["project_id"], user, "parse", f"{doc['name']} -> {parsed['status']}")
    return RedirectResponse(f"/projects/{doc['project_id']}?tab=docs&msg=解析完成：{parsed['status']}", status_code=303)


@app.post("/projects/{pid}/compare/{cid}")
def update_compare(pid: int, cid: int, request: Request, settle_qty: str = Form(""), user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    get_project(conn, pid, user)
    conn.execute("UPDATE compare_items SET settle_qty=?, status='待确认' WHERE id=? AND project_id=?", (settle_qty, cid, pid))
    conn.commit()
    audit(conn, pid, user, "compare", f"更新 {cid} 结算量={settle_qty}")
    return RedirectResponse(f"/projects/{pid}?tab=compare&msg=已更新，请复核", status_code=303)


@app.post("/projects/{pid}/ai")
def create_ai(pid: int, request: Request, scene: str = Form("设计资料解析"), user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    project = get_project(conn, pid, user)
    prompt, output, engine = generate_draft(conn, project, scene)
    conn.execute(
        "INSERT INTO ai_outputs (project_id, scene, prompt, output, engine, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (pid, scene, prompt, output, engine, "初稿", user["id"], now()),
    )
    conn.commit()
    audit(conn, pid, user, "ai_generate", f"{scene} / {engine}")
    return RedirectResponse(f"/projects/{pid}?tab=ai&msg=已生成初稿，请复核", status_code=303)


@app.post("/ai/{aid}/review")
def review_ai(aid: int, request: Request, result: str = Form("通过"), user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    out = conn.execute("SELECT * FROM ai_outputs WHERE id=?", (aid,)).fetchone()
    if not out:
        raise HTTPException(404)
    get_project(conn, out["project_id"], user)
    conn.execute("INSERT INTO reviews (ai_output_id, project_id, item, result, reviewer, reviewed_at) VALUES (?,?,?,?,?,?)", (aid, out["project_id"], "AI输出复核", result, user["name"], now()))
    conn.execute("UPDATE ai_outputs SET status=? WHERE id=?", ("已复核" if result == "通过" else "退回", aid))
    conn.commit()
    audit(conn, out["project_id"], user, "review", f"AI #{aid} {result}")
    return RedirectResponse(f"/projects/{out['project_id']}?tab=ai&msg=复核已记录", status_code=303)


@app.get("/export/{pid}/{kind}")
def export_csv(pid: int, kind: str, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    get_project(conn, pid, user)
    if kind == "materials":
        rows = conn.execute("SELECT supply, code, name, spec, unit, qty, source FROM materials WHERE project_id=?", (pid,)).fetchall()
        headers = ["供应", "物资编码", "名称", "规格", "单位", "数量", "来源"]
    elif kind == "boq":
        rows = conn.execute("SELECT code, name, unit, qty, part FROM boq WHERE project_id=?", (pid,)).fetchall()
        headers = ["编号", "名称", "单位", "数量", "部位"]
    elif kind == "compare":
        rows = conn.execute("SELECT item, unit, design_qty, budget_qty, settle_qty, reason, evidence, status FROM compare_items WHERE project_id=?", (pid,)).fetchall()
        headers = ["项目", "单位", "设计量", "预算量", "结算量", "差异原因", "依据", "状态"]
    else:
        raise HTTPException(404)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([r[k] for k in r.keys()])
    data = "\ufeff" + buf.getvalue()
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={kind}-{pid}.csv"})

@app.get("/export/{pid}/xlsx/{kind}")
def export_xlsx(pid: int, kind: str, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    get_project(conn, pid, user)
    wb = Workbook()
    ws = wb.active
    ws.title = {"materials": "材料清册", "boq": "工程量", "compare": "三算对比", "budget": "预算"}.get(kind, kind)
    if kind == "materials":
        headers = ["供应", "物资编码", "名称", "规格", "单位", "数量", "来源"]
        rows = conn.execute("SELECT supply, code, name, spec, unit, qty, source FROM materials WHERE project_id=?", (pid,)).fetchall()
    elif kind == "boq":
        headers = ["编号", "名称", "单位", "数量", "部位"]
        rows = conn.execute("SELECT code, name, unit, qty, part FROM boq WHERE project_id=?", (pid,)).fetchall()
    elif kind == "compare":
        headers = ["项目", "单位", "设计量", "预算量", "结算量", "差异原因", "依据", "状态"]
        rows = conn.execute("SELECT item, unit, design_qty, budget_qty, settle_qty, reason, evidence, status FROM compare_items WHERE project_id=?", (pid,)).fetchall()
    elif kind == "budget":
        headers = ["项目", "金额（元）", "类别"]
        rows = conn.execute("SELECT name, amount, category FROM budget_items WHERE project_id=?", (pid,)).fetchall()
    else:
        raise HTTPException(404)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8F0FE")
    for r in rows:
        ws.append([r[k] for k in r.keys()])
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[chr(64 + i)].width = max(12, min(28, len(h) * 3 + 8))
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={kind}-{pid}.xlsx"},
    )
