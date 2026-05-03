""" Whatsapp client to collect user input image/text/audio"""
import os
from pywa import WhatsApp, types
from pywa.types import Message
from fastapi import FastAPI
from pywa import WhatsApp, filters
from dotenv import load_dotenv
from supabase import create_client, Client
from fpdf import FPDF
from datetime import datetime

import google.generativeai as genai
import PIL.Image
import json

load_dotenv("ice_breaker/.env")
app = FastAPI()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
# Using the flash model because it is incredibly fast and great at reading text from images
vision_model = genai.GenerativeModel('gemini-2.5-flash')

PHONE_NUMBER = os.getenv("PHONE_NUMBER_ID")
WA_TOKEN = os.getenv("WA_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

wa = WhatsApp(
    phone_id=PHONE_NUMBER,
    token=WA_TOKEN,
    server=app,
    verify_token=VERIFY_TOKEN
)


user_sessions = {}

def create_invoice_pdf(invoice_id, name, phone, items_list, total):
    """Generates a PDF for multiple items."""
    pdf = FPDF()
    pdf.add_page()
    
    # Header
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt="TAX INVOICE", ln=True, align='C')
    pdf.ln(5)
    
    # Details
    pdf.set_font("Arial", size=10)
    pdf.cell(200, 6, txt=f"Invoice ID: #{invoice_id}", ln=True)
    pdf.cell(200, 6, txt=f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
    pdf.cell(200, 6, txt=f"Customer Name: {name}", ln=True)
    pdf.cell(200, 6, txt=f"Phone: {phone}", ln=True)
    pdf.cell(200, 6, txt="-"*50, ln=True)
    
    # Line Items Loop
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(90, 6, txt="Item", border=0)
    pdf.cell(30, 6, txt="Qty", border=0)
    pdf.cell(40, 6, txt="Rate", border=0)
    pdf.cell(30, 6, txt="Subtotal", ln=True, border=0)
    
    pdf.set_font("Arial", size=10)
    for item in items_list:
        subtotal = item['qty'] * item['price']
        pdf.cell(90, 6, txt=item['name'][:40]) # truncate long names
        pdf.cell(30, 6, txt=str(item['qty']))
        pdf.cell(40, 6, txt=f"Rs {item['price']:.2f}")
        pdf.cell(30, 6, txt=f"Rs {subtotal:.2f}", ln=True)
        
    # Total
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 10, txt=f"Total (incl. GST): Rs. {total:.2f}", ln=True)
    
    filename = f"invoice_{invoice_id}.pdf"
    pdf.output(filename)
    return filename


# --- BUSINESS LOGIC ---
def generate_bill(sku_code, quantity):
    """Looks up the product and calculates the final price."""
    response = supabase.table('products').select('*').eq('sku', str(
        sku_code)).execute()
    
    if not response.data:
        return f"❌ Error: SKU {sku_code} not found in database."
        
    product = response.data[0]
    final_total = (product['price'] * quantity) * 1.18
    
    return (
        f"🧾 *Draft Bill*\n"
        f"Item: {quantity}x {product['name']}\n"
        f"SKU: {sku_code}\n"
        f"Total: ₹{final_total:.2f} (incl. GST)\n\n"
        f"Correct? Reply *Yes* or *Edit*"
    )


# print( PHONE_NUMBER, WA_TOKEN, VERIFY_TOKEN)

def confirm_and_log_sale(name, phone, items_list, total_count, total_amount):
    """Logs the multi-item sale into the new Supabase invoices table."""
    invoice_data = {
        "customer_name": name,
        "phone_number": phone,
        "items": items_list,  # Supabase will automatically convert this Python list to JSONB
        "total_item_count": total_count,
        "total_amount": total_amount
    }
    try:
        response = supabase.table('invoices').insert(invoice_data).execute()
        if response.data:
            return response.data[0]['id']
    except Exception as e:
        print(f"Database Error: {e}")


# 3. Han# --- WEBHOOK LISTENERS ---
@wa.on_message(filters.text)
def handle_text(client: WhatsApp, msg: Message):
    incoming_text = msg.text.strip().lower()
    sender_phone = msg.from_user.wa_id
    
    # 🧠 CHECK IF USER IS IN AN ACTIVE SESSION
    if sender_phone in user_sessions:
        session = user_sessions[sender_phone]
        
        # --- STATE: WAITING FOR 'YES' OR 'EDIT' ---
        if session['state'] == "draft":
            if incoming_text == "yes":
                if sender_phone in user_sessions:
                    order = user_sessions[sender_phone]
                    msg.reply_text("✅ Confirmed! Logging multi-item sale and generating PDF...")
                    
                    # Pass the unified data to Supabase
                    invoice_id = confirm_and_log_sale(
                        order['customer_name'], 
                        order['phone_number'], 
                        order['items'], 
                        order['total_count'], 
                        order['total_amount']
                    )
                    
                    if invoice_id:
                        # Pass the unified data to the PDF Generator
                        pdf_filename = create_invoice_pdf(
                            invoice_id, 
                            order['customer_name'], 
                            order['phone_number'], 
                            order['items'], 
                            order['total_amount']
                        )
                        msg.reply_document(document=pdf_filename, filename=pdf_filename, caption=f"Receipt #{invoice_id}")
                        if os.path.exists(pdf_filename): os.remove(pdf_filename)
                        del user_sessions[sender_phone] 
                    else:
                        msg.reply_text("❌ Database Error.")
                    return    
            elif incoming_text == "edit":
                session['state'] = "editing_choice"
                msg.reply_text("✏️ What do you want to edit?\n1. Quantity\n2. Price per item\n\nReply *1* or *2*")
                return

        # --- STATE: WAITING FOR EDIT CHOICE (1 OR 2) ---
        elif session['state'] == "editing_choice":
            if incoming_text == "1":
                session['state'] = "editing_qty"
                msg.reply_text("🔢 Enter the new quantity:")
            elif incoming_text == "2":
                session['state'] = "editing_price"
                msg.reply_text("💰 Enter the new price per item (₹):")
            else:
                msg.reply_text("⚠️ Please reply *1* for Quantity or *2* for Price.")
            return

        # --- STATE: WAITING FOR NEW VALUE ---
        elif session['state'] in ["editing_qty", "editing_price"]:
            try:
                new_value = float(incoming_text)
                
                # Update the specific field
                if session['state'] == "editing_qty":
                    session['qty'] = int(new_value)
                elif session['state'] == "editing_price":
                    session['base_price'] = new_value
                
                # Recalculate total with 18% GST
                session['total'] = (session['base_price'] * session['qty']) * 1.18
                session['state'] = "draft" # Send them back to the draft state!
                
                # Send the updated draft
                msg.reply_text(
                    f"🔄 *Updated Draft Bill*\n"
                    f"Item: {session['qty']}x {session['name']}\n"
                    f"Total: ₹{session['total']:.2f} (incl. GST)\n\n"
                    f"Correct? Reply *Yes* or *Edit*"
                )
            except ValueError:
                msg.reply_text("⚠️ Please enter a valid number.")
            return

    # --- NO ACTIVE SESSION: ASSUME NEW ORDER (e.g., "10 5") ---
    try:
        parts = incoming_text.split()
        if len(parts) != 2:
            msg.reply_text("⚠️ Please send the format: [SKU] [Quantity]\nExample: 10 5")
            return
            
        sku = parts[0]
        qty = int(parts[1])
        
        response = supabase.table('products').select('*').eq('sku', str(sku)).execute()
        if not response.data:
            msg.reply_text(f"❌ Error: SKU {sku} not found.")
            return
        
        product = response.data[0]
        base_price = product['price']
        base_price = product['selling_price']
        final_total = (base_price * qty) * 1.18
        
        # 🧠 INITIALIZE MEMORY WITH STATE
        user_sessions[sender_phone] = {
            "customer_name": "Walk-in Customer",
            "phone_number": sender_phone,
            "items": [{"name": product['item_name'], "qty": qty, "price": base_price}],
            "total_count": qty,
            "total_amount": (base_price * qty) * 1.18,
            "state": "draft"
        }
        
        msg.reply_text(
            f"🧾 *Draft Bill*\n"
            f"Item: {qty}x {product['name']}\n"
            f"Total: ₹{final_total:.2f} (incl. GST)\n\n"
            f"Correct? Reply *Yes* or *Edit*"
        )
        
    except ValueError:
        msg.reply_text("⚠️ Quantity must be a number. Example: 10 5")
    
    # print(f"\n[TEXT RECEIVED] From: {sender_phone} | Content: {incoming_text}")
    
    # # For now, just echo back to test the connection
    # msg.reply_text(f"I received your text: {incoming_text}")


# 4. Handle Image Messages (e.g., Uploading a new price list)
@wa.on_message(filters.image)
def handle_image(client: WhatsApp, msg: Message):
    sender_phone = msg.from_user.wa_id
    msg.reply_text("📸 Image received! I am analyzing the bill now...")

    try:
        # 1. Download the image from Meta's servers temporarily
        # pywa handles the secure download automatically!
        image_path = msg.image.download(filename="temp_bill.jpg")
        # img = PIL.Image.open(image_path)
        

        # 2. Ask the AI to extract exactly what we need in JSON format
        prompt = """
        You are a highly accurate data extraction system. Read this bill/invoice.
        Extract the data and return it in exactly this JSON format. DO NOT return any conversational text, only the JSON block.
        {
          "customer_phone": "extract number if available, otherwise return null",
          "customer_name": "extract name if available, otherwise return null",
          "items": [
            {"name": "item 1 name", "qty": 2, "price": 100.0}
          ],
          "total_amount": 200.0
        }
        """
        
        with PIL.Image.open(image_path) as img:
            response = vision_model.generate_content([prompt, img])
        
        # 3. Clean and parse the AI's JSON response
        raw_text = response.text.strip()
        # Remove markdown formatting if the AI added it (e.g., ```json ... ```)
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3]
        
        extracted_data = json.loads(raw_text)
        
        # 4. Display the extracted data to the owner for confirmation
        total_items = len(extracted_data['items'])
        calc_total = sum(item['qty'] * item['price'] for item in extracted_data['items'])
        
        # Override the total just in case the math on the handwritten bill was wrong!
        final_total = calc_total * 1.18 # Add our 18% GST standard
        
        # 🧠 SAVE TO MEMORY SO THEY CAN SAY "YES"
        # Ensure we have fallback values if AI couldn't find them
        cust_name = extracted_data.get('customer_name') or "Walk-in Customer"
        cust_phone = extracted_data.get('customer_phone') or sender_phone

        # 🧠 UNIFIED MEMORY FOR AI
        user_sessions[sender_phone] = {
            "customer_name": cust_name,
            "phone_number": cust_phone,
            "items": extracted_data['items'], # E.g., [{"name": "pipe", "qty": 2, "price": 100}]
            "total_count": total_items,
            "total_amount": final_total,
            "state": "draft"
        }
        
        # 5. Send the draft back to WhatsApp
        draft_msg = (
            f"✅ *AI Extraction Complete*\n"
            f"Found {total_items} items.\n"
            f"Base Math: ₹{calc_total:.2f}\n"
            f"Total with 18% GST: ₹{final_total:.2f}\n\n"
            f"Log this to the database and generate PDF? Reply *Yes* or *Edit*"
        )
        msg.reply_text(draft_msg)
        print(image_path)
        # Cleanup the downloaded image

    except Exception as e:
        print(f"Vision Error: {e}")
        msg.reply_text("❌ Sorry, I couldn't read the bill clearly. Please ensure the image is bright and readable.")

    finally:
        if os.path.exists(image_path):
            os.remove(image_path)

# 5. Handle Audio/Voice Messages
@wa.on_message(filters.audio | filters.voice)
def handle_audio(client: WhatsApp, msg: Message):
    sender_phone = msg.from_user.wa_id
    msg.reply_text("🎙️ Audio received! Listening and transcribing...")

    try:
        # 1. Download the audio file (pywa handles both voice notes and audio files)
        audio_obj = msg.voice or msg.audio
        audio_path = audio_obj.download(filename="temp_audio.ogg")

        # 2. Upload the audio to Google's temporary processing server
        # (Unlike images, Gemini requires audio files to be uploaded via their File API first)
        ai_audio_file = genai.upload_file(path=audio_path)

        # 3. Prompt the AI
        prompt = """
        Listen to this voice note from a shop owner dictating an order. 
        Extract the items, quantities, and prices. If they don't mention a price, use 0.0.
        Return it in exactly this JSON format. DO NOT return any conversational text, only the JSON block.
        {
          "customer_phone": "extract number if spoken, otherwise return null",
          "customer_name": "extract name if spoken, otherwise return null",
          "items": [
            {"name": "item 1", "qty": 5, "price": 120.0}
          ]
        }
        """
        
        # 4. Generate the response
        response = vision_model.generate_content([prompt, ai_audio_file])
        
        # 5. Clean and parse the JSON
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3]
            
        extracted_data = json.loads(raw_text)
        
        # 6. Calculate Totals
        total_items = len(extracted_data['items'])
        calc_total = sum(item['qty'] * item['price'] for item in extracted_data['items'])
        final_total = calc_total * 1.18 # 18% GST
        
        # 🧠 SAVE TO MEMORY
        # Ensure we have fallback values if AI couldn't find them
        cust_name = extracted_data.get('customer_name') or "Walk-in Customer"
        cust_phone = extracted_data.get('customer_phone') or sender_phone

        # 🧠 UNIFIED MEMORY FOR AI
        user_sessions[sender_phone] = {
            "customer_name": cust_name,
            "phone_number": cust_phone,
            "items": extracted_data['items'], # E.g., [{"name": "pipe", "qty": 2, "price": 100}]
            "total_count": total_items,
            "total_amount": final_total,
            "state": "draft"
        }
        
        
        # 7. Send the Draft back
        draft_msg = (
            f"✅ *Voice Processing Complete*\n"
            f"Heard {total_items} items.\n"
            f"Base Math: ₹{calc_total:.2f}\n"
            f"Total with 18% GST: ₹{final_total:.2f}\n\n"
            f"Log this and generate PDF? Reply *Yes* or *Edit*"
        )
        msg.reply_text(draft_msg)

        # 8. Safe Cleanup (Delete local file & Google Cloud file)
        if os.path.exists(audio_path):
            os.remove(audio_path)
        ai_audio_file.delete()

    except Exception as e:
        print(f"Audio Error: {e}")
        msg.reply_text("❌ Sorry, I couldn't understand the audio clearly. Please try speaking a bit slower.")
        
        # Fallback local cleanup
        if os.path.exists("temp_audio.ogg"):
            try:
                os.remove("temp_audio.ogg")
            except:
                pass

        