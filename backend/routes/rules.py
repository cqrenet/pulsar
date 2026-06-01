from auth import require_auth
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from models.api import AlertRuleResponse
from rules import rules_collection

router = APIRouter(dependencies=[Depends(require_auth)])


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get("/rules", response_model=list[AlertRuleResponse])
def list_rules():
    return [_serialize(doc) for doc in rules_collection.find()]


@router.post("/rules", response_model=AlertRuleResponse)
def create_rule(rule: AlertRuleResponse):
    payload = rule.model_dump(exclude={"id"})
    result = rules_collection.insert_one(payload)
    payload["id"] = str(result.inserted_id)
    return payload


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
def update_rule(rule_id: str, rule: AlertRuleResponse):
    payload = rule.model_dump(exclude={"id"})
    result = rules_collection.update_one({"_id": ObjectId(rule_id)}, {"$set": payload})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {**payload, "id": rule_id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str):
    result = rules_collection.delete_one({"_id": ObjectId(rule_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True}
