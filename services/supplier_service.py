from dependencies import supabase

class SupplierService:
    @staticmethod
    def get_all(business_id: str):
        response = supabase.table("suppliers") \
            .select("*") \
            .eq("business_id", business_id) \
            .eq("is_active", True) \
            .execute()
        return response.data

    @staticmethod
    def create(payload: dict):
        response = supabase.table("suppliers").insert(payload).execute()
        return response.data

    @staticmethod
    def update(supplier_id: str, business_id: str, updates: dict):
        response = supabase.table("suppliers") \
            .update(updates) \
            .eq("supplier_id", supplier_id) \
            .eq("business_id", business_id) \
            .execute()
        return response.data

    @staticmethod
    def soft_delete(supplier_id: str, business_id: str):
        response = supabase.table("suppliers") \
            .update({"is_active": False}) \
            .eq("supplier_id", supplier_id) \
            .eq("business_id", business_id) \
            .execute()
        return response.data