"""
nursing-erp-query handler — Python helper functions for calling nursing-erp API.

Used by agents via the `process` tool:

    python3 -c "
    import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-erp-query')
    from handler import search_residents
    result = search_residents(building='1号楼', care_level='全护')
    print(result)
    "
"""

import json
import os

import httpx

DEFAULT_URL = "http://nursing-erp:8080"


def _client():
    url = os.environ.get("NURSING_ERP_URL", DEFAULT_URL)
    return httpx.Client(base_url=url, timeout=15)


def _print_json(data):
    """Pretty-print JSON for agent readability."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---- 老人照护 ----

def search_residents(building=None, care_level=None, search=None):
    """查询老人列表。"""
    params = {}
    if building:
        params["building"] = building
    if care_level:
        params["care_level"] = care_level
    if search:
        params["search"] = search
    r = _client().get("/api/residents/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def get_resident(resident_id: int):
    """查询老人详情。"""
    r = _client().get(f"/api/residents/{resident_id}/")
    r.raise_for_status()
    _print_json(r.json())


def get_resident_logs(resident_id: int, log_date: str = None):
    """查询护理日志。"""
    params = {}
    if log_date:
        params["log_date"] = log_date
    r = _client().get(f"/api/residents/{resident_id}/logs/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def get_resident_health(resident_id: int):
    """查询健康数据。"""
    r = _client().get(f"/api/residents/{resident_id}/health/")
    r.raise_for_status()
    _print_json(r.json())


def get_resident_medications(resident_id: int):
    """查询用药记录。"""
    r = _client().get(f"/api/residents/{resident_id}/medications/")
    r.raise_for_status()
    _print_json(r.json())


# ---- 人员管理 ----

def search_employees(dept=None, is_caregiver=None):
    """查询员工列表。"""
    params = {}
    if dept:
        params["dept"] = dept
    if is_caregiver is not None:
        params["is_caregiver"] = str(is_caregiver).lower()
    r = _client().get("/api/employees/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def get_employee_attendance(employee_id: int, start_date=None, end_date=None):
    """查询员工考勤。"""
    params = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    r = _client().get(f"/api/employees/{employee_id}/attendance/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def search_schedules(date=None, building=None):
    """查询排班。"""
    params = {}
    if date:
        params["date"] = date
    if building:
        params["building"] = building
    r = _client().get("/api/schedules/", params=params)
    r.raise_for_status()
    _print_json(r.json())


# ---- 院内事务 ----

def search_inventory(category=None):
    """查询库存。"""
    params = {}
    if category:
        params["category"] = category
    r = _client().get("/api/inventory/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def get_low_stock():
    """低库存预警。"""
    r = _client().get("/api/inventory/low-stock/")
    r.raise_for_status()
    _print_json(r.json())


def search_maintenance(status=None):
    """查询报修工单。"""
    params = {}
    if status:
        params["status"] = status
    r = _client().get("/api/maintenance/", params=params)
    r.raise_for_status()
    _print_json(r.json())


# ---- 异常上报 ----

def search_incidents(severity=None, handled=None, category=None):
    """查询异常上报。"""
    params = {}
    if severity:
        params["severity"] = severity
    if handled is not None:
        params["handled"] = str(handled).lower()
    if category:
        params["category"] = category
    r = _client().get("/api/incidents/", params=params)
    r.raise_for_status()
    _print_json(r.json())


# ---- 点餐送餐 ----

def search_meal_plans(date=None):
    """查询菜单。"""
    params = {}
    if date:
        params["date"] = date
    r = _client().get("/api/meal-plans/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def search_meal_orders(date=None, status=None, meal_type=None):
    """查询点餐订单。"""
    params = {}
    if date:
        params["date"] = date
    if status:
        params["status"] = status
    if meal_type:
        params["meal_type"] = meal_type
    r = _client().get("/api/meal-orders/", params=params)
    r.raise_for_status()
    _print_json(r.json())


def search_meal_finance(month=None):
    """查询餐费月结。"""
    params = {}
    if month:
        params["month"] = month
    r = _client().get("/api/meal-finance/", params=params)
    r.raise_for_status()
    _print_json(r.json())
