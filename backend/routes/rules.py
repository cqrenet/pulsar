from audit_trail import log_action
from auth import require_admin, require_auth
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from models.api import AlertRule
from rules import rules_collection
from utils.common import user_sub

router = APIRouter(dependencies=[Depends(require_auth)])


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get("/rules", response_model=list[AlertRule])
def list_rules():
    return [_serialize(doc) for doc in rules_collection.find()]


@router.post("/rules", response_model=AlertRule)
def create_rule(rule: AlertRule, user: dict = Depends(require_admin)):
    payload = rule.model_dump(exclude={"id"})
    result = rules_collection.insert_one(payload)
    payload["id"] = str(result.inserted_id)
    log_action("create_rule", "/api/rules", {"name": rule.name, "enabled": rule.enabled}, user_sub(user))
    return payload


@router.put("/rules/{rule_id}", response_model=AlertRule)
def update_rule(rule_id: str, rule: AlertRule, user: dict = Depends(require_admin)):
    if not ObjectId.is_valid(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    payload = rule.model_dump(exclude={"id"})
    result = rules_collection.update_one({"_id": ObjectId(rule_id)}, {"$set": payload})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    log_action("update_rule", f"/api/rules/{rule_id}", payload, user_sub(user))
    return {**payload, "id": rule_id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, user: dict = Depends(require_admin)):
    if not ObjectId.is_valid(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    rule = rules_collection.find_one_and_delete({"_id": ObjectId(rule_id)})
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    log_action("delete_rule", f"/api/rules/{rule_id}", {"name": rule.get("name")}, user_sub(user))
    return {"deleted": True}
