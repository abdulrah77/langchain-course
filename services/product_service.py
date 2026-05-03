from dependencies import supabase

class ProductService:
    @staticmethod
    def get_all(business_id: str):
        response = supabase.table("products") \
            .select("*, product_variants(*)") \
            .eq("business_id", business_id) \
            .eq("is_active", True) \
            .execute()
        return response.data

    @staticmethod
    def create(payload: dict):
        response = supabase.table("products").insert(payload).execute()
        return response.data

    @staticmethod
    def update(product_id: str, business_id: str, updates: dict):
        response = supabase.table("products") \
            .update(updates) \
            .eq("product_id", product_id) \
            .eq("business_id", business_id) \
            .execute()
        return response.data

    @staticmethod
    def soft_delete(product_id: str, business_id: str):
        response = supabase.table("products") \
            .update({"is_active": False}) \
            .eq("product_id", product_id) \
            .eq("business_id", business_id) \
            .execute()
        return response.data