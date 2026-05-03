from __future__ import annotations

from typing import Any, Dict

from dependencies import supabase


class InventoryService:
    @staticmethod
    def get_all(business_id: str, allowed_stores: list[str], limit: int = 50, skip: int = 0, search: str = None, low_stock: bool = False):
        if not allowed_stores:
            return []

        query = supabase.table("inventory") \
            .select("*, products!inner(product_name, sku_code)") \
            .eq("business_id", business_id) \
            .in_("store_id", allowed_stores) \
            .eq("is_active", True)

        # 1. Filtering: Low Stock Alert
        if low_stock:
            # Supabase doesn't support comparing two columns directly in the standard SDK easily,
            # but we can fetch and filter, or create a Postgres View. 
            # For simplicity, we'll filter where stock < 10 (or whatever static number you prefer for now)
            query = query.lt("stock_quantity", 10)

        # 2. Searching: Search by Product Name or SKU
        if search:
            query = query.ilike("products.product_name", f"%{search}%")

        # 3. Pagination (Supabase uses 0-indexed ranges: 0 to 49 = 50 items)
        query = query.range(skip, skip + limit - 1)

        response = query.execute()
        return response.data

    @staticmethod
    def create(payload: dict):
        required_fields = {"business_id", "store_id", "product_id", "stock_quantity", "buy_price", "selling_price"}
        missing = [field for field in required_fields if field not in payload]
        if missing:
            raise ValueError(f"Missing required fields for inventory create: {', '.join(missing)}")

        initial_stock = float(payload.get("stock_quantity", 0))
        row = dict(payload)
        row["stock_quantity"] = 0
        response = supabase.table("inventory").insert(row).execute()
        if not response.data:
            raise ValueError("Failed to create inventory row")
        created = response.data[0]
        if initial_stock != 0:
            InventoryService.create_inventory_transaction(
                {
                    "business_id": created["business_id"],
                    "store_id": created["store_id"],
                    "inventory_id": created["inventory_id"],
                    "transaction_type": "purchase" if initial_stock > 0 else "adjustment",
                    "quantity": abs(initial_stock) if initial_stock > 0 else initial_stock,
                    "reference_type": "inventory_create",
                    "reference_id": created["inventory_id"],
                    "note": "Opening stock from inventory creation",
                }
            )
        return [InventoryService._get_inventory_by_id(created["inventory_id"], created["business_id"], created["store_id"])]

    @staticmethod
    def update(inventory_id: str, business_id: str, store_id: str, updates: dict):
        if "stock_quantity" in updates:
            raise ValueError("Direct stock updates are disabled. Use inventory transaction endpoint.")
        response = (
            supabase.table("inventory")
            .update(updates)
            .eq("inventory_id", inventory_id) \
            .eq("business_id", business_id) \
            .eq("store_id", store_id)
            .execute()
        )
        return response.data

    @staticmethod
    def soft_delete(inventory_id: str, business_id: str, store_id: str):
        response = supabase.table("inventory") \
            .update({"is_active": False}) \
            .eq("inventory_id", inventory_id) \
            .eq("business_id", business_id) \
            .eq("store_id", store_id) \
            .execute()
        return response.data
    
    @staticmethod
    def get_dashboard_stats(business_id: str, allowed_stores: list[str]):
        if not allowed_stores:
            return {
                "total_unique_items": 0,
                "total_stock_units": 0,
                "total_cost_value": 0,
                "total_retail_value": 0,
                "potential_profit": 0,
                "low_stock_alerts": 0
            }

        # Fetch only the numbers we need to save bandwidth
        response = supabase.table("inventory") \
            .select("stock_quantity, buy_price, selling_price") \
            .eq("business_id", business_id) \
            .in_("store_id", allowed_stores) \
            .eq("is_active", True) \
            .execute()
        
        data = response.data
        
        total_items = len(data)
        total_stock_units = sum(item["stock_quantity"] for item in data)
        
        # Calculate potential revenue and cost (fallback to 0 if price is missing)
        total_cost_value = sum(item["stock_quantity"] * (item["buy_price"] or 0) for item in data)
        total_retail_value = sum(item["stock_quantity"] * (item["selling_price"] or 0) for item in data)
        
        low_stock_count = sum(1 for item in data if item["stock_quantity"] < 10)

        return {
            "total_unique_items": total_items,
            "total_stock_units": total_stock_units,
            "total_cost_value": round(total_cost_value, 2),
            "total_retail_value": round(total_retail_value, 2),
            "potential_profit": round(total_retail_value - total_cost_value, 2),
            "low_stock_alerts": low_stock_count
        }

    @staticmethod
    def _get_inventory_by_id(inventory_id: str, business_id: str, store_id: str) -> Dict[str, Any]:
        response = (
            supabase.table("inventory")
            .select("*")
            .eq("inventory_id", inventory_id)
            .eq("business_id", business_id)
            .eq("store_id", store_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError("Inventory item not found in scope")
        return response.data[0]

    @staticmethod
    def create_inventory_transaction(payload: dict) -> Dict[str, Any]:
        required_fields = {"business_id", "store_id", "inventory_id", "transaction_type", "quantity"}
        missing = [field for field in required_fields if field not in payload]
        if missing:
            raise ValueError(f"Missing required fields for transaction: {', '.join(missing)}")
        quantity = float(payload["quantity"])
        if quantity == 0:
            raise ValueError("quantity cannot be zero")

        rpc_response = supabase.rpc("apply_inventory_transaction", {"p_payload": payload}).execute()
        if not rpc_response.data:
            raise ValueError("Transaction application failed")
        return rpc_response.data

    @staticmethod
    def adjust_stock_to(
        *,
        inventory_id: str,
        business_id: str,
        store_id: str,
        target_stock_quantity: float,
        reference_type: str = "manual_adjustment",
        reference_id: str | None = None,
        note: str | None = None,
    ) -> Dict[str, Any]:
        current = InventoryService._get_inventory_by_id(inventory_id, business_id, store_id)
        delta = round(float(target_stock_quantity) - float(current["stock_quantity"]), 3)
        if delta == 0:
            return {"inventory_id": inventory_id, "product_id": current["product_id"], "stock_quantity": target_stock_quantity}
        return InventoryService.create_inventory_transaction(
            {
                "business_id": business_id,
                "store_id": store_id,
                "inventory_id": inventory_id,
                "transaction_type": "adjustment",
                "quantity": delta,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "note": note or "Stock adjusted to target quantity",
            }
        )

    @staticmethod
    def verify_stock_consistency(business_id: str, store_id: str | None = None):
        return supabase.rpc(
            "verify_inventory_stock_consistency",
            {"p_business_id": business_id, "p_store_id": store_id},
        ).execute().data

    @staticmethod
    def save_whatsapp_entry(business_id: str, store_id: str, data: dict):
        product_name = data["product_name"]
        base_price = data["base_price"]
        variants = data.get("variants", [])

        # Helper function to save a specific item
        def insert_or_update(name_suffix: str, qty: int, price: float):
            full_name = f"{product_name} - {name_suffix}".strip(" - ")
            
            prod_response = supabase.table("products") \
                .select("product_id").eq("business_id", business_id).ilike("product_name", full_name).execute()
            
            if prod_response.data:
                product_id = prod_response.data[0]["product_id"]
                inv_response = (
                    supabase.table("inventory")
                    .select("inventory_id,stock_quantity")
                    .eq("product_id", product_id)
                    .eq("business_id", business_id)
                    .eq("store_id", store_id)
                    .limit(1)
                    .execute()
                )
                if inv_response.data:
                    InventoryService.create_inventory_transaction(
                        {
                            "business_id": business_id,
                            "store_id": store_id,
                            "inventory_id": inv_response.data[0]["inventory_id"],
                            "transaction_type": "purchase",
                            "quantity": qty,
                            "reference_type": "whatsapp_inventory",
                            "reference_id": product_id,
                            "note": "Stock added via WhatsApp inventory flow",
                        }
                    )
            else:
                new_prod = supabase.table("products").insert({
                    "business_id": business_id,
                    "product_name": full_name.title(),
                    "sku_code": f"WA-{full_name[:5].upper()}-{qty}"
                }).execute()
                
                created_inventory = supabase.table("inventory").insert({
                    "business_id": business_id,
                    "store_id": store_id,
                    "product_id": new_prod.data[0]["product_id"],
                    "stock_quantity": 0,
                    "buy_price": price,
                    "selling_price": price * 1.5,
                    "is_active": True
                }).execute()
                InventoryService.create_inventory_transaction(
                    {
                        "business_id": business_id,
                        "store_id": store_id,
                        "inventory_id": created_inventory.data[0]["inventory_id"],
                        "transaction_type": "purchase",
                        "quantity": qty,
                        "reference_type": "whatsapp_inventory",
                        "reference_id": created_inventory.data[0]["inventory_id"],
                        "note": "Opening stock from WhatsApp inventory flow",
                    }
                )

        # Execute Logic: If variants exist, save them individually. 
        # If no variants, just save the main product.
        if variants:
            for v in variants:
                v_price = v.get("price") if v.get("price") else base_price
                insert_or_update(v["description"], v["quantity"], v_price)
        else:
            insert_or_update("", data["total_quantity"], base_price)