# import os
# import json
# import logging
# from datetime import datetime
# from zoneinfo import ZoneInfo # For accurate Indian Standard Time

# from fastapi import FastAPI
# from pywa import WhatsApp, filters
# from pywa.types import Message
# from dotenv import load_dotenv
# from supabase import create_client, Client
# from fpdf import FPDF
# import google.generativeai as genai
# import PIL.Image

# # --- CONFIGURATION & INIT ---
# load_dotenv("ice_breaker/.env")
# logging.basicConfig(level=logging.INFO)

# app = FastAPI()
# genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
# vision_model = genai.GenerativeModel('gemini-2.5-flash')

# PHONE_NUMBER = os.getenv("PHONE_NUMBER_ID")
# WA_TOKEN = os.getenv("WA_TOKEN")
# VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
# SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
# SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")

# supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# wa = WhatsApp(phone_id=PHONE_NUMBER, token=WA_TOKEN, server=app, verify_token=VERIFY_TOKEN)

# # In-memory session state
# user_sessions = {}


# def analyze_intent(content_list):
#     """
#     Passes text, image, or audio to Gemini to determine the user's intent:
#     1. generate_bill
#     2. add_product
#     3. track_cash
#     """
#     master_prompt = """
#     You are the manager of a retail shop. Analyze the user's input (audio, image, or text) and determine their intent.
#     Return strictly a JSON object with two keys: "intent" and "data". DO NOT return markdown formatting.

#     Possible Intents & Expected Data Formats:

#     1. "generate_bill": If the user is listing items to sell to a customer.
#        Data format: {"customer": "Name/Null", "items": [{"name": "item", "qty": 2, "price": 100}]}
       
#     2. "add_product": If the user is adding new stock or updating catalog prices.
#        Data format: {"name": "Product Name", "sku": "SKU123", "price": 150}
       
#     3. "track_cash": If the user is logging an expense, taking money out, or moving money.
#        Data format: {"type": "expense" | "cash_taken" | "cash_to_gpay" | "debit_credit", "amount": 500, "description": "tea for staff"}
#     """
    
#     try:
#         response = vision_model.generate_content([master_prompt] + content_list)
#         raw_text = response.text.strip().replace("```json", "").replace("```", "")
#         return json.loads(raw_text)
#     except Exception as e:
#         logging.error(f"AI Router Error: {e}")
#         return None
    

# # --- A. SECURITY & DB HELPERS ---
# def authenticate_user(sender_phone: str):
#     """Auth Bouncer: Silently drops unauthorized numbers."""
#     try:
#         # Note: WhatsApp numbers include country code (e.g., 919876543210)
#         response = supabase.table("employees").select("id, business_id").eq("contact_number", sender_phone).eq("is_active", True).execute()
#         if response.data:
#             return response.data[0]
#         return None
#     except Exception as e:
#         logging.error(f"Auth Database Error: {e}")
#         return None

# def confirm_and_log_sale(business_id, employee_id, name, phone, items_list, total_count, total_amount):
#     """Logs the sale to Supabase."""
#     invoice_data = {
#         "business_id": business_id,
#         "employee_id": employee_id,
#         "customer_phone": phone, 
#         "items": items_list,
#         "total_item_count": total_count,
#         "total_amount": total_amount
#     }
#     try:
#         response = supabase.table('invoices').insert(invoice_data).execute()
#         if response.data:
#             return response.data[0]['id']
#     except Exception as e:
#         logging.error(f"Database Error: {e}")
#     return None

# def create_invoice_pdf(invoice_id, business_id, name, phone, items_list, total):
#     """Generates a PDF using standard fonts (Unicode safe for Rs.)"""
#     pdf = FPDF()
#     pdf.add_page()
    
#     # Use IST for the generated PDF
#     ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    
#     pdf.set_font("Arial", 'B', 16)
#     pdf.cell(200, 10, txt="TAX INVOICE", ln=True, align='C')
#     pdf.ln(5)
    
#     pdf.set_font("Arial", size=10)
#     pdf.cell(200, 6, txt=f"Business ID: {business_id}", ln=True)
#     pdf.cell(200, 6, txt=f"Invoice ID: #{invoice_id}", ln=True)
#     pdf.cell(200, 6, txt=f"Date: {ist_now.strftime('%Y-%m-%d %I:%M %p')}", ln=True)
#     pdf.cell(200, 6, txt=f"Customer Name: {name}", ln=True)
#     pdf.cell(200, 6, txt=f"Customer Phone: {phone}", ln=True)
#     pdf.cell(200, 6, txt="-"*50, ln=True)
    
#     # Table Header
#     pdf.set_font("Arial", 'B', 10)
#     pdf.cell(90, 6, txt="Item", border=0)
#     pdf.cell(30, 6, txt="Qty", border=0)
#     pdf.cell(40, 6, txt="Rate", border=0)
#     pdf.cell(30, 6, txt="Subtotal", ln=True, border=0)
    
#     # Table Rows
#     pdf.set_font("Arial", size=10)
#     for item in items_list:
#         subtotal = item['qty'] * item['price']
#         pdf.cell(90, 6, txt=str(item['name'])[:40])
#         pdf.cell(30, 6, txt=str(item['qty']))
#         pdf.cell(40, 6, txt=f"Rs {item['price']:.2f}")
#         pdf.cell(30, 6, txt=f"Rs {subtotal:.2f}", ln=True)
        
#     pdf.ln(5)
#     pdf.set_font("Arial", 'B', 12)
#     pdf.cell(200, 10, txt=f"Grand Total (incl. GST): Rs. {total:.2f}", ln=True)
    
#     filename = f"invoice_{invoice_id}.pdf"
#     pdf.output(filename)
#     return filename


# # --- B. WEBHOOK LISTENERS ---

# @wa.on_message(filters.text)
# def handle_text(client: WhatsApp, msg: Message):
#     sender_phone = msg.from_user.wa_id
#     user = authenticate_user(sender_phone)
#     if not user: return # Unauthorized

#     incoming_text = msg.text.strip().lower()
    
#     # 1. HELP & ONBOARDING MENU
#     if incoming_text in ["hi", "hello", "help", "menu", "start"]:
#         help_msg = (
#             "👋 *Welcome to your Smart Billing Bot!*\n\n"
#             "Here is how you can generate bills:\n"
#             "📸 *Snap a photo* of a handwritten bill.\n"
#             "🎙️ *Send a voice note* (e.g., '2 pipes at 100 rupees').\n"
#             "⌨️ *Type a code* (e.g., 'SKU123 5' for 5 units of SKU123).\n\n"
#             "If you make a mistake, just type *Cancel*."
#         )
#         msg.reply_text(help_msg)
#         return

#     # 2. CANCEL / RESET FLOW
#     if incoming_text in ["cancel", "reset", "stop"]:
#         if sender_phone in user_sessions:
#             del user_sessions[sender_phone]
#         msg.reply_text("🚫 Current draft cleared. Ready for a new order!")
#         return

#     # 3. ACTIVE SESSION HANDLING (Confirmation)
#     if sender_phone in user_sessions:
#         session = user_sessions[sender_phone]
        
#         if session['state'] == "draft" and incoming_text == "yes":
#             msg.reply_text("✅ Confirmed! Generating Official PDF Receipt...")
            
#             invoice_id = confirm_and_log_sale(
#                 user['business_id'], user['id'],
#                 session['customer_name'], session['phone_number'], 
#                 session['items'], session['total_count'], session['total_amount']
#             )
            
#             if invoice_id:
#                 pdf_filename = create_invoice_pdf(
#                     invoice_id, user['business_id'],
#                     session['customer_name'], session['phone_number'], 
#                     session['items'], session['total_amount']
#                 )
#                 msg.reply_document(document=pdf_filename, filename=pdf_filename, caption=f"🧾 Receipt #{invoice_id} attached.")
#                 if os.path.exists(pdf_filename): os.remove(pdf_filename)
#                 del user_sessions[sender_phone] 
#             else:
#                 msg.reply_text("❌ Database Error. Please contact support.")
#             return    
#         else:
#             msg.reply_text("⚠️ You have a draft bill waiting. Reply *Yes* to confirm or *Cancel* to start over.")
#             return

#     # 4. MANUAL TEXT ORDER ENTRY
#     parts = incoming_text.split()
#     if len(parts) == 2:
#         try:
#             sku, qty = parts[0], int(parts[1])
#             response = supabase.table('products').select('*').eq('business_id', user['business_id']).eq('sku_code', str(sku)).execute()
            
#             if not response.data:
#                 msg.reply_text(f"❌ Product with SKU '{sku}' not found in your catalog.")
#                 return
            
#             product = response.data[0]
#             base_price = product['selling_price']
#             final_total = (base_price * qty) * 1.18 # 18% GST
            
#             user_sessions[sender_phone] = {
#                 "business_id": user['business_id'], "employee_id": user['id'],
#                 "customer_name": "Walk-in Customer", "phone_number": sender_phone,
#                 "items": [{"name": product['item_name'], "qty": qty, "price": base_price}],
#                 "total_count": qty, "total_amount": final_total, "state": "draft"
#             }
            
#             msg.reply_text(
#                 f"🧾 *Draft Bill*\n"
#                 f"Item: {qty}x {product['item_name']}\n"
#                 f"Total: ₹{final_total:.2f} (incl. 18% GST)\n\n"
#                 f"Correct? Reply *Yes* or *Cancel*"
#             )
#         except ValueError:
#             msg.reply_text("⚠️ Format error. Please use: [SKU] [Quantity] (e.g., A100 5)")
#     else:
#         msg.reply_text("🤔 I didn't understand that. Type *Help* to see what I can do.")

# from pywa.types import Button, CallbackButton

# @wa.on_message(filters.image)
# def handle_image_estimate(client: WhatsApp, msg: Message):
#     sender_phone = msg.from_user.wa_id
#     user = authenticate_user(sender_phone)
#     if not user: return

#     msg.reply_text("📸 Scanning document... Please wait.")
#     image_path = msg.image.download(filename=f"temp_bill_{sender_phone}.jpg")

#     try:
#         # Simplified Prompt: Just get the data, don't guess the intent.
#         prompt = """
#         Read this handwritten or printed bill/estimate. Extract data into this exact strict JSON format:
#         {
#           "customer_phone": "number or null",
#           "customer_name": "name or null",
#           "items": [{"name": "item name", "qty": 2, "price": 100.0}]
#         }
#         """
#         with PIL.Image.open(image_path) as img:
#             response = vision_model.generate_content([prompt, img])
        
#         raw_text = response.text.strip().replace("```json", "").replace("```", "")
#         extracted_data = json.loads(raw_text)
        
#         if not extracted_data.get('items'):
#             raise ValueError("No items found.")
            
#         total_items = len(extracted_data['items'])
        
#         # SAVE TO MEMORY AS 'PENDING_INTENT'
#         user_sessions[sender_phone] = {
#             "business_id": user['business_id'],
#             "employee_id": user['id'],
#             "extracted_data": extracted_data, # Store the raw AI output
#             "state": "waiting_for_intent"
#         }
        
#         # SEND WHATSAPP INTERACTIVE BUTTONS
#         msg.reply_text(
#             text=f"✅ Scanned {total_items} items successfully.\n\nWhat would you like to do with this data?",
#             buttons=[
#                 Button(title="🧾 Customer Bill", callback_data="intent_bill"),
#                 Button(title="📦 Add to Stock", callback_data="intent_stock"),
#                 Button(title="🚫 Cancel", callback_data="intent_cancel")
#             ]
#         )

#     except Exception as e:
#         logging.error(f"Vision Error: {e}")
#         msg.reply_text("❌ Sorry, I couldn't read the items clearly. Please try again.")
#     finally:
#         if os.path.exists(image_path): os.remove(image_path)

# @wa.on_callback_button()
# def handle_button_clicks(client: WhatsApp, btn: CallbackButton):
#     sender_phone = btn.from_user.wa_id
    
#     # 1. Check if they have an active session waiting for routing
#     if sender_phone not in user_sessions or user_sessions[sender_phone]['state'] != "waiting_for_intent":
#         btn.reply_text("⚠️ No active scan found. Please upload the image again.")
#         return

#     session = user_sessions[sender_phone]
#     data = session['extracted_data']
    
#     # --- ROUTE: CANCEL ---
#     if btn.data == "intent_cancel":
#         del user_sessions[sender_phone]
#         btn.reply_text("🚫 Cancelled. Memory cleared.")
#         return

#     # --- ROUTE: ADD TO INVENTORY ---
#     elif btn.data == "intent_stock":
#         items_added = 0
#         try:
#             for item in data.get('items', []):
#                 sku = item.get('sku') or item['name'][:5].upper()
#                 supabase.table('products').upsert({
#                     "business_id": session['business_id'],
#                     "item_name": item['name'],
#                     "sku_code": sku,
#                     "purchase_price": item.get('price', 0),
#                     "selling_price": item.get('price', 0) * 1.3 # Example Markup
#                 }, on_conflict="business_id, sku_code").execute()
#                 items_added += 1
            
#             btn.reply_text(f"📦 *Inventory Updated*\nSuccessfully added {items_added} items to your stock.")
#             del user_sessions[sender_phone] # Clear memory
            
#         except Exception as e:
#             btn.reply_text(f"❌ Database error: {e}")

#     # --- ROUTE: GENERATE CUSTOMER BILL ---
#     elif btn.data == "intent_bill":
#         items = data.get('items', [])
#         total_items = len(items)
#         calc_total = sum(item['qty'] * item['price'] for item in items)
#         final_total = calc_total * 1.18 # 18% GST
        
#         # Advance the state machine to "draft" waiting for final "Yes"
#         user_sessions[sender_phone] = {
#             "business_id": session['business_id'],
#             "employee_id": session['employee_id'],
#             "customer_name": data.get('customer_name') or "Walk-in Customer",
#             "phone_number": data.get('customer_phone') or sender_phone,
#             "items": items,
#             "total_count": total_items,
#             "total_amount": final_total,
#             "state": "draft"
#         }
        
#         btn.reply_text(
#             f"✅ *Customer Draft Bill Ready*\n"
#             f"Total items: {total_items}\n"
#             f"Total with 18% GST: ₹{final_total:.2f}\n\n"
#             f"Reply *Yes* to generate the official PDF receipt, or *Cancel*."
#         )


# # --- B. UPDATED WEBHOOK HANDLER (Example for Voice) ---

# @wa.on_message(filters.audio | filters.voice)
# def handle_audio_erp(client: WhatsApp, msg: Message):
#     sender_phone = msg.from_user.wa_id
#     user = authenticate_user(sender_phone)
#     if not user: return

#     msg.reply_text("🎙️ Processing your voice command...")
#     audio_obj = msg.voice or msg.audio
#     audio_path = audio_obj.download(filename=f"temp_audio_{sender_phone}.ogg")

#     try:
#         ai_audio_file = genai.upload_file(path=audio_path)
        
#         # 1. SEND TO AI ROUTER
#         parsed_result = analyze_intent([ai_audio_file])
        
#         if not parsed_result:
#             msg.reply_text("❌ Couldn't understand the command. Please try again.")
#             return

#         intent = parsed_result['intent']
#         data = parsed_result['data']

#         # ---------------------------------------------------------
#         # ROUTE 1: INVENTORY MANAGEMENT
#         # Example Voice: "Add new product, half inch pipe, SKU P50, price 120."
#         # ---------------------------------------------------------
#         if intent == "add_product":
#             try:
#                 # Insert or Update the products table
#                 supabase.table('products').upsert({
#                     "business_id": user['business_id'],
#                     "item_name": data['name'],
#                     "sku_code": data['sku'].upper(),
#                     "selling_price": data['price']
#                 }, on_conflict="business_id, sku_code").execute()
                
#                 msg.reply_text(f"📦 *Inventory Updated*\nAdded '{data['name']}' (SKU: {data['sku'].upper()}) at ₹{data['price']}.")
#             except Exception as e:
#                 msg.reply_text(f"❌ Database error saving product: {e}")

#         # ---------------------------------------------------------
#         # ROUTE 2: CASH TRACKER
#         # Example Voice: "Took 500 rupees from the register for auto fare."
#         # ---------------------------------------------------------
#         elif intent == "track_cash":
#             try:
#                 # Convert out-flowing cash to negative amounts for the DB ledger
#                 is_outflow = data['type'] in ['expense', 'cash_taken', 'cash_to_gpay']
#                 amount = float(data['amount'])
#                 if is_outflow: amount = -abs(amount)
                
#                 supabase.table('cash_ledger').insert({
#                     "business_id": user['business_id'],
#                     "employee_id": user['id'],
#                     "transaction_type": data['type'],
#                     "amount": amount,
#                     "description": data['description']
#                 }).execute()
                
#                 msg.reply_text(f"💸 *Ledger Updated*\nLogged {data['type']}: ₹{abs(amount)}\nMemo: {data['description']}")
#             except Exception as e:
#                 msg.reply_text(f"❌ Database error logging cash: {e}")

#         # ---------------------------------------------------------
#         # ROUTE 3: INVOICE GENERATOR
#         # Example Voice: "Two pipes at 50 rupees for walk-in customer."
#         # ---------------------------------------------------------
#         elif intent == "generate_bill":
#              # [Insert the exact same Draft Bill / Memory Session logic we built in the previous step here]
#              msg.reply_text(f"🧾 *Draft Bill created for {len(data['items'])} items.* Reply 'Yes' to confirm.")

#         else:
#             msg.reply_text("🤔 I understood the words, but I don't know what action to take.")

#     except Exception as e:
#         logging.error(f"Routing Pipeline Error: {e}")
#         msg.reply_text("❌ A system error occurred while processing.")
#     finally:
#         if os.path.exists(audio_path): os.remove(audio_path)
#         try: ai_audio_file.delete() 
#         except: pass

import os
import logging

from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from routers import cash_ledger, inventory, invoices, products, public_links, suppliers, whatsapp, purchases, returns
from routers import webhooks as razorpay_webhooks
from routers import onboarding

from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from services.subscription_reminder_job import run_subscription_reminders

_scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start daily subscription reminder job (runs at 09:00 IST every day)
    _scheduler.add_job(
        run_subscription_reminders,
        trigger="cron",
        hour=3,          # 03:30 UTC = 09:00 IST
        minute=30,
        id="subscription_reminders",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("scheduler_started")
    yield
    _scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")

app = FastAPI(
    title="SaaS Inventory API",
    description="Multi-tenant inventory management system",
    version="1.0.0",
    lifespan=lifespan,
)
logger = logging.getLogger("ice_breaker.api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory.router)
app.include_router(products.router)
app.include_router(suppliers.router)
app.include_router(whatsapp.router)
app.include_router(invoices.router)
app.include_router(purchases.router)
app.include_router(returns.router)
app.include_router(cash_ledger.router)
app.include_router(public_links.router)
app.include_router(razorpay_webhooks.router)
app.include_router(onboarding.router)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    import uuid as _uuid
    request_id = request.headers.get("x-request-id") or str(_uuid.uuid4())
    logger.info(
        "http_request_start method=%s path=%s request_id=%s",
        request.method, request.url.path, request_id,
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request_end method=%s path=%s status_code=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


@app.get("/")
async def verify_webhook_root(
    hub_mode_dot: str | None = Query(None, alias="hub.mode"),
    hub_verify_token_dot: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge_dot: str | None = Query(None, alias="hub.challenge"),
    hub_mode_underscore: str | None = Query(None, alias="hub_mode"),
    hub_verify_token_underscore: str | None = Query(None, alias="hub_verify_token"),
    hub_challenge_underscore: str | None = Query(None, alias="hub_challenge"),
):
    """
    Compatibility endpoint: some Meta verification attempts may hit root path.
    Accept both dot-style and underscore-style query parameter names.
    """
    hub_mode = hub_mode_dot or hub_mode_underscore
    hub_verify_token = hub_verify_token_dot or hub_verify_token_underscore
    hub_challenge = hub_challenge_dot or hub_challenge_underscore

    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if hub_mode == "subscribe" and hub_verify_token == verify_token and hub_challenge:
        return PlainTextResponse(content=hub_challenge)

    raise HTTPException(status_code=404, detail="Not Found")


@app.post("/")
async def receive_webhook_root_compat(request: Request, background_tasks: BackgroundTasks):
    """
    Compatibility endpoint: some Meta app setups post to root URL.
    Keep webhook processing stable by routing POST / to the same worker.
    """
    payload = await request.json()
    logger.info("root_webhook_post_received")

    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        if "messages" not in value:
            logger.info("root_webhook_ignored_no_messages")
            return {"status": "ignored"}

        message = value["messages"][0]
        sender_phone = message.get("from")
        msg_type = message.get("type")

        logger.info(
            "root_webhook_handoff sender=%s msg_type=%s",
            sender_phone,
            msg_type,
        )
        background_tasks.add_task(whatsapp.process_webhook_logic, message, sender_phone, msg_type)
        return {"status": "success"}
    except Exception as e:
        logger.exception("root_webhook_error error=%s", e)
        return {"status": "error"}


@app.get("/health")
async def health_check():
    return {"status": "ok", "system": "operational"}
