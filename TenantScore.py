from fastapi import FastAPI, Form, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import re, os, smtplib, textwrap, traceback, asyncio
from datetime import datetime
from email.message import EmailMessage
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from textwrap import wrap
import fitz  # PyMuPDF

# --- Configuration ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "devin@adeptwell.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

app = FastAPI()

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper Functions ---

def extract_text(uploaded_file: UploadFile):
    try:
        uploaded_file.file.seek(0)
        if uploaded_file.filename.endswith(".pdf"):
            with open("temp.pdf", "wb") as f:
                f.write(uploaded_file.file.read())
            doc = fitz.open("temp.pdf")
            text = "".join(page.get_text() for page in doc)
            doc.close()
            return text
        return uploaded_file.file.read().decode("utf-8", errors="ignore")
    except:
        return ""

async def summarize_doc_async(label, content):
    if not content.strip():
        return f"No readable content found in {label}."
    prompt = f"Summarize the following document titled '{label}':\n\n{content[:3000]}"
    try:
        response = await asyncio.to_thread(client.chat.completions.create,
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        )
        return response.choices[0].message.content.strip()
    except:
        return f"Could not summarize {label}."

def score_tenant(form_data, doc_insights):
    prompt = f"""You're a leasing analyst. Review the following tenant application and provide a numeric TenantScore (0–100), a risk tier, and two GPT-written summaries.

Risk Tiers:
🟩 Low Risk = 2+ years, strong financials, clean docs  
🟧 Stable but New = 0–2 years, solid but early  
🟨 Moderate Risk = financial inconsistencies  
🟥 High Risk = low revenue, high liabilities, weak docs  

Instructions:
- If the business meets the Low Risk criteria above, do not downgrade to Stable but New.
- Format your response exactly as:
TenantScore: #
Risk Tier: [Low Risk / Stable but New / Moderate Risk / High Risk]
Lease Summary: ...
Industry Insight: ...

Form Data:
- Name: {form_data['tenant_name']}
- Business: {form_data['business_name']}
- Industry: {form_data['business_type']}
- Years in Business (Stated): {form_data['years_experience']}
- Monthly Revenue: {form_data['monthly_revenue']}
- Cash on Hand: {form_data['cash_reserve']}
- Rent Budget: {form_data['rent_budget']}

Uploaded Document Summaries:
- Profit & Loss: {doc_insights['Profit & Loss']}
- Tax Return – Year 1: {doc_insights['Tax Return – Year 1']}
- Tax Return – Year 2: {doc_insights['Tax Return – Year 2']}
- PFS: {doc_insights['PFS']}
- Business Plan: {doc_insights['Business Plan']}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        ).choices[0].message.content.strip()

        print("RAW GPT RESPONSE:\n", response)

        score_match = re.search(r"TenantScore[:\s]*([0-9]{1,3})", response)
        risk_match = re.search(r"Risk Tier[:\s]*[^a-zA-Z]*(Low Risk|Stable but New|Moderate Risk|High Risk)", response)
        summary_match = re.search(r"Lease Summary[:\s]*(.+?)Industry Insight", response, re.DOTALL)
        industry_match = re.search(r"Industry Insight[:\s]*(.+)", response, re.DOTALL)

        score = int(score_match.group(1)) if score_match else 0
        risk_level = risk_match.group(1) if risk_match else "Unknown"
        summary = summary_match.group(1).strip() if summary_match else "Summary unavailable."
        industry = industry_match.group(1).strip() if industry_match else "Industry insight unavailable."

    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()
        score = 0
        risk_level = "Unknown"
        summary = "Summary unavailable."
        industry = "Industry insight unavailable."

    return score, risk_level, summary, industry

def send_email(report_path, filename, business_name, agent_email):
    msg = EmailMessage()
    msg["From"] = EMAIL_SENDER
    msg["To"] = agent_email
    msg["Subject"] = f"TenantScore Report for {business_name}"
    msg.set_content(f"Attached is the TenantScore report for {business_name}.\n\n— Powered by Adeptwell")
    with open(report_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=filename)
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)

def create_base_pdf(data, filename, uploaded_files):
    base_path = os.path.join("reports", filename)
    os.makedirs("reports", exist_ok=True)
    c = canvas.Canvas(base_path, pagesize=LETTER)
    width, height = LETTER
    y = height - 70

    # --- Branding based on agent_key ---
    agent_key = data.get("agent_key", "").lower()

    if agent_key == "andy":
        header_color = colors.HexColor("#B2D7F0")  # Andy blue
        text_color = colors.HexColor("#0A3C66")
        header_title = "TenantScore Report – Andy Moffitt | Mountain West"
    elif agent_key == "tanner":
        header_color = colors.HexColor("#A7B84B")  # Tanner green
        text_color = colors.black
        header_title = "TenantScore Report – Tanner Olson | Legend Partners"
    else:
        header_color = colors.HexColor("#7E7B46")  # Default
        text_color = colors.black
        header_title = "TenantScore Report"
    # ------------------------------------

    # Header
    c.setFillColor(header_color)
    c.rect(0, height - 50, width, 50, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 30, header_title)
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 40, height - 30, f"Submitted: {datetime.now().strftime('%B %d, %Y')}")
    c.setFillColor(text_color)

    def footer():
        c.setFont("Helvetica-Oblique", 9)
        c.setFillColor(header_color)
        c.drawString(40, 40, "Powered by Adeptwell | TenantScore")
        c.setFillColor(text_color)

    def check_page_space(lines_needed=1):
        nonlocal y
        if y - (lines_needed * 14) < 60:
            footer()
            c.showPage()
            y = height - 70

    def write_section(title, content):
        nonlocal y
        check_page_space(3)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, title)
        y -= 18
        c.setFont("Helvetica", 10)
        wrapped = wrap(content, 95)
        for line in wrapped:
            check_page_space(1)
            c.drawString(50, y, line)
            y -= 14
        y -= 10

    # --- Write sections ---
    write_section("Applicant Info", 
        f"Name: {data['tenant_name']} | Phone: {data['tenant_phone']} | Email: {data['tenant_email']}\n"
        f"Business: {data['business_name']} | Type: {data['business_type']}\n"
        f"Experience: {data['years_experience']} yrs | Revenue: ${data['monthly_revenue']} | Cash: ${data['cash_reserve']}\n"
        f"Rent Budget: ${data['rent_budget']}"
    )
    write_section("AI Evaluation", f"TenantScore: {data['score']}/100\nRisk Level: {data['risk_level']}")
    write_section("Lease Summary", data['summary'])
    write_section("Industry Insight", data['industry_insight'])

    for section, insight in data['doc_insights'].items():
        write_section(section, insight)

    if data.get("certn_status"):
        write_section("Background Check", f"Certn Status: {data['certn_status']}")

    footer()
    c.save()

    # Embed uploaded docs
    final = fitz.open(base_path)
    for upload in uploaded_files:
        if upload:
            upload.file.seek(0)
            with open("temp_upload.pdf", "wb") as f:
                f.write(upload.file.read())
            try:
                pages = fitz.open("temp_upload.pdf")
                final.insert_pdf(pages)
                pages.close()
            except Exception as e:
                print(f"❌ Failed to embed {upload.filename}: {e}")

    temp_path = os.path.join("reports", f"temp_{filename}")
    final.save(temp_path)
    final.close()
    os.replace(temp_path, base_path)
    return base_path

# --- Main Endpoint ---

@app.post("/generate-score")
async def generate_score(
    background_tasks: BackgroundTasks,
    tenant_name: str = Form(...),
    tenant_phone: str = Form(...),
    tenant_email: str = Form(...),
    agent_email: str = Form(...),
    business_name: str = Form(...),
    business_type: str = Form(...),
    years_experience: int = Form(...),
    monthly_revenue: float = Form(...),
    cash_reserve: float = Form(...),
    rent_budget: float = Form(...),
    dob: str = Form(...),
    address: str = Form(...),
    city: str = Form(...),
    state: str = Form(...),
    zip_code: str = Form(...),
    run_certn: bool = Form(False),
    pl_file: UploadFile = File(...),
    tax_return_1_file: UploadFile = File(...),
    tax_return_2_file: UploadFile = File(...),
    business_plan_file: UploadFile = File(None),
    pfs_file: UploadFile = File(None),
    agent_key: str = Form("default")
):
    try:
        summaries = await asyncio.gather(
            summarize_doc_async("P&L", extract_text(pl_file)),
            summarize_doc_async("Tax Year 1", extract_text(tax_return_1_file)),
            summarize_doc_async("Tax Year 2", extract_text(tax_return_2_file)),
            summarize_doc_async("Business Plan", extract_text(business_plan_file)) if business_plan_file else asyncio.sleep(0),
            summarize_doc_async("PFS", extract_text(pfs_file)) if pfs_file else asyncio.sleep(0),
        )

        doc_insights = {
            "Profit & Loss": summaries[0],
            "Tax Return – Year 1": summaries[1],
            "Tax Return – Year 2": summaries[2],
            "Business Plan": summaries[3] if business_plan_file else "Business plan not provided.",
            "PFS": summaries[4] if pfs_file else "PFS not provided."
        }

        form_data = {
            "tenant_name": tenant_name,
            "business_name": business_name,
            "business_type": business_type,
            "years_experience": years_experience,
            "monthly_revenue": monthly_revenue,
            "cash_reserve": cash_reserve,
            "rent_budget": rent_budget,
            "tenant_phone": tenant_phone,
            "tenant_email": tenant_email,
            "agent_key": agent_key
        }

        score, risk_level, summary, industry_insight = score_tenant(form_data, doc_insights)

        filename = f"TenantScore_{business_name.replace(' ', '_')}.pdf"
        report_path = create_base_pdf({
            **form_data,
            "score": score,
            "risk_level": risk_level,
            "summary": summary,
            "industry_insight": industry_insight,
            "doc_insights": doc_insights,
            "certn_status": "Pending"
        }, filename, [
            pl_file, tax_return_1_file, tax_return_2_file, pfs_file, business_plan_file
        ])

        background_tasks.add_task(send_email, report_path, filename, business_name, agent_email)

        return JSONResponse(content={
            "score": score,
            "risk_level": risk_level,
            "summary": summary,
            "industry_insight": industry_insight,
            "pdf_file": filename
        })

    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)