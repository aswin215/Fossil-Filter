"""Generate a deterministic SAMPLE dataset so the pipeline runs before you export your real Gmail.

Writes:
  data/raw/sample.mbox            ~100 fake emails (incl. LinkedIn job alerts, attachments, odd charsets)
  data/labels.csv                 sample labels for the student_jobhunter persona  (replace with yours!)
  data/job_gold.csv               sample job fit labels for the jobs inside the alerts (replace with yours!)
  data/synthetic_sensitive.jsonl  60 sensitive + 60 benign emails for the guardrail benchmark

Everything here is fabricated. Labels are the generator's intent, not a human's judgement.
"""
from __future__ import annotations

import csv
import json
import mailbox
import random
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

R = random.Random(42)
ME = "alex.student@example.edu"
BASE = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
FIRST = ["Priya", "Jordan", "Wei", "Maria", "Sam", "Daniel", "Aisha", "Kenji", "Elena", "Omar", "Lucas", "Nina"]
LAST = ["Shah", "Lee", "Chen", "Garcia", "Patel", "Kim", "Nguyen", "Okafor", "Rossi", "Haddad"]
COS = ["Nimbus AI", "Tessellate", "Brightpath", "Quantia", "Lumen Labs", "Arcwise", "Fernway", "Orbitly",
       "Datacrest", "Kiln Robotics", "Helix Health", "Northwind Systems"]


def name() -> str:
    return f"{R.choice(FIRST)} {R.choice(LAST)}"


def when(i: int) -> datetime:
    return BASE + timedelta(hours=i * 3 + R.randint(0, 2), minutes=R.randint(0, 59))


# (category, action, from, subject, body)   placeholders filled with .format(**ctx)
T = {
    "urgent_action_needed": [
        ("reply_today", "{n} <{u}@{co_d}>", "Please confirm your interview slot by EOD today",
         "Hi Alex,\n\nThanks for applying to the Machine Learning Intern role at {co}. We'd like to schedule your "
         "technical interview. Please confirm one of the slots below by end of day today, otherwise we will need to "
         "release them:\n\n- Thu 2pm PT\n- Fri 10am PT\n\nBest,\n{n}\nUniversity Recruiting, {co}"),
        ("reply_today", "Prof. {last} <{u}@example.edu>", "Thesis draft feedback - need your response before tomorrow",
         "Alex,\n\nI've reviewed chapter 3. There are two issues with the evaluation section that must be fixed "
         "before the committee deadline tomorrow at noon. Can you reply today with a plan?\n\n- Prof. {last}"),
        ("add_to_calendar", "{co} Recruiting <recruiting@{co_d}>", "Action required: online assessment expires in 24 hours",
         "Your HackerRank assessment for the Software Engineer, New Grad position at {co} expires within 24 hours. "
         "Complete it before {day} 11:59pm PT. This is your final reminder."),
        ("reply_today", "{n} <{u}@{co_d}>", "Offer deadline Friday - can we talk today?",
         "Hi Alex, congratulations again on the offer for the Data Science Intern role. The offer expires this "
         "Friday. Could you let me know today if you have any questions or need an extension?"),
        ("reply_today", "Graduate Admissions <gradoffice@example.edu>", "Immediate action: OPT/CPT form missing signature",
         "Your CPT authorization form is missing your advisor signature. Your internship start date is at risk. "
         "Please upload the signed form immediately; the processing deadline is tomorrow."),
        ("add_to_calendar", "{n} <{u}@example.edu>", "Group project demo moved to TODAY 4pm",
         "Heads up team - the professor moved our capstone demo to today at 4pm in Room 210. Please be there 15 "
         "minutes early. Reply if you can't make it."),
    ],
    "important": [
        ("track_as_application", "{co} Careers <no-reply@{co_d}>", "Your application for Machine Learning Engineer Intern",
         "Hi Alex,\n\nThank you for applying to {co}. We have received your application for Machine Learning "
         "Engineer Intern (Summer 2027). Our team is reviewing candidates and will reach out with next steps."),
        ("track_as_application", "{co} Talent <talent@{co_d}>", "Update on your application - moving to next steps",
         "Good news! Your application for Backend Software Engineer, New Grad has moved to the next stage. A "
         "recruiter will contact you within the next week to schedule a phone screen."),
        ("read_later", "Prof. {last} <{u}@example.edu>", "Comments on your research proposal",
         "Hi Alex, I left comments on your proposal in the shared doc. Overall the direction on retrieval models "
         "is good. Let's discuss at our next meeting; no rush."),
        ("add_to_calendar", "{n} <{u}@example.edu>", "Advisor meeting next Tuesday 3pm",
         "Hi Alex, confirming our 1:1 advisor meeting next Tuesday at 3pm in my office. Bring your updated "
         "results. Calendar invite attached."),
        ("track_as_application", "{co} Recruiting <recruiting@{co_d}>", "Unfortunately - your application to {co}",
         "Hi Alex, thank you for your interest in the Software Engineering Intern role. After careful review we "
         "have decided to move forward with other candidates. We encourage you to apply again."),
        ("reply_today", "{n} <{u}@gmail.com>", "Referral for the {co} new grad role?",
         "Hey Alex! I saw you're looking at {co}. I work on their ML platform team and can refer you for the new "
         "grad role. Can you send me your resume and the job link?"),
        ("read_later", "TA Coordinator <ta-office@example.edu>", "TA assignment for Spring semester",
         "You've been assigned as a teaching assistant for CS 146 (Data Structures) next semester. Details about "
         "orientation will follow."),
    ],
    "informational": [
        ("read_later", "The Batch <newsletter@deeplearning.example>", "The Batch: new open-weight models, weekly recap",
         "This week in AI: new open-weight models, a recap of the latest benchmarks, and an article on agent "
         "evaluation. Read the full newsletter on our site."),
        ("archive", "GitHub <notifications@github.com>", "[repo] CI run succeeded on main",
         "All checks have passed for commit a1b2c3d on main. This is an automated notification."),
        ("read_later", "CS Department <cs-news@example.edu>", "Department newsletter - November",
         "Highlights: faculty awards, upcoming research talks, and a recap of the hackathon. Weekly digest from the "
         "department."),
        ("archive", "Amazon <shipment-tracking@amazon.example>", "Your order has shipped",
         "Your order of USB-C hub has shipped and will arrive Thursday. Track your package in Your Orders."),
        ("read_later", "Medium Daily Digest <noreply@medium.example>", "Daily digest: 5 stories on system design",
         "Stories for you: Designing rate limiters, Kafka in practice, and more. Read later from your digest."),
        ("archive", "Campus Events <events@example.edu>", "Reminder: career fair this Thursday",
         "FYI the fall engineering career fair is Thursday in the Student Union. Over 80 employers attending. "
         "No registration needed."),
        ("archive", "LinkedIn <messages-noreply@linkedin.com>", "{n} viewed your profile",
         "{n}, a recruiter at {co}, viewed your profile. See who else viewed your profile this week."),
        ("read_later", "Kaggle <noreply@kaggle.example>", "New competition announcement",
         "A new featured competition on tabular forecasting launched today. Community notebooks and a recap of "
         "last month's winners inside."),
    ],
    "job_alert": [
        ("read_later", "Handshake <jobs@handshake.example>", "5 new jobs for you this week",
         "Recommended jobs based on your profile: Data Engineering Intern at {co}, ML Research Intern at {co2}, "
         "Software Engineer I at {co3}. Apply now on Handshake."),
        ("read_later", "Indeed <alert@indeed.example>", "New jobs: python developer in San Jose, CA",
         "New jobs matching your search 'python developer': Python Developer at {co} (Full-time), Backend Engineer "
         "at {co2}. Apply now."),
    ],
    "promotion_or_spam": [
        ("ignore", "TechGear <deals@techgear.example>", "URGENT: 60% off ends TONIGHT",
         "Last chance! Our biggest sale of the year ends tonight. 60% off headphones and laptops. Shop now. "
         "Unsubscribe here."),
        ("ignore", "Prize Center <winner@prizes.example>", "Congratulations you have been selected!",
         "Congratulations you are our lucky winner of a $1000 gift card. Claim your prize now with this exclusive "
         "offer. Limited time."),
        ("archive", "Coursera <promo@coursera.example>", "Exclusive offer: 40% off Coursera Plus",
         "Upskill for less. Get 40% off Coursera Plus for a limited time. Use coupon LEARN40. Unsubscribe anytime."),
        ("ignore", "Pizza Palace <promo@pizza.example>", "Free shipping weekend + BOGO deal",
         "This weekend only: buy one get one free on large pizzas and free delivery. Promo code BOGO. Unsubscribe."),
        ("archive", "Notion <team@notion.example>", "Introducing new AI features - try them free",
         "We've launched new AI features in Notion. Try them free for 30 days. Exclusive offer for students."),
        ("ignore", "Crypto Signals <vip@signals.example>", "Make $5k/week - act now",
         "Join our VIP crypto signals group. Members earn $5k a week. Act now, limited spots. Unsubscribe."),
    ],
}
COUNTS = {"urgent_action_needed": 14, "important": 22, "informational": 24, "job_alert": 5,
          "promotion_or_spam": 20}

# --------------------------------------------------------------------------- LinkedIn job alerts
ROLES = [
    # title, seniority, type, skills, notes, gold fit for student_jobhunter
    ("Machine Learning Engineer Intern", "Internship", "Internship", "Python, PyTorch, MLOps", "", "high"),
    ("Software Engineer Intern - Backend", "Internship", "Internship", "Go, Python, PostgreSQL, Kubernetes", "", "high"),
    ("New Grad Software Engineer, ML Platform", "Entry level", "Full-time", "Python, Spark, Kubernetes", "Visa sponsorship available", "high"),
    ("Data Science Intern", "Internship", "Internship", "Python, SQL, scikit-learn", "", "high"),
    ("AI Research Intern (LLMs)", "Internship", "Internship", "PyTorch, Transformers, CUDA", "", "high"),
    ("Backend Engineer I (New Grad)", "Entry level", "Full-time", "Java, Spring, AWS", "", "medium"),
    ("Frontend Engineer Intern", "Internship", "Internship", "React, TypeScript, CSS", "", "medium"),
    ("QA Automation Intern", "Internship", "Internship", "Selenium, Python", "", "medium"),
    ("Data Analyst, Entry Level", "Entry level", "Full-time", "SQL, Tableau, Excel", "", "medium"),
    ("Software Engineer Intern - Defense Systems", "Internship", "Internship", "C++, Linux", "Active security clearance required; US citizens only", "low"),
    ("Senior Machine Learning Engineer", "Mid-Senior level", "Full-time", "Python, TensorFlow, AWS", "5+ years of production ML experience", "low"),
    ("Staff Backend Engineer", "Mid-Senior level", "Full-time", "Go, Distributed Systems", "8+ years experience", "low"),
    ("ML Engineer, New Grad", "Entry level", "Full-time", "Python, PyTorch", "No sponsorship available for this role", "low"),
    ("Principal Data Scientist", "Director", "Full-time", "Python, Causal Inference", "10+ years experience", "low"),
    ("Sales Development Representative", "Entry level", "Full-time", "Salesforce, Cold calling", "", "low"),
    ("Embedded Software Intern", "Internship", "Internship", "C, RTOS, Embedded Linux", "", "medium"),
]
CITIES = ["San Jose, CA", "San Francisco, CA", "Remote", "Seattle, WA", "Mountain View, CA", "New York, NY"]
ALERT_SUBJ = ["{t}: {k} new jobs at {co} and more", "Jobs you may be interested in: {t}",
              "{co} is hiring: {t}", "Your job alert for {t}"]


def linkedin_alert(i: int, job_gold: list) -> tuple[EmailMessage, dict]:
    picks = R.sample(ROLES, R.randint(4, 6))
    cards, plain = [], []
    jobs_meta = []
    for title, sen, typ, skills, notes, fit in picks:
        co = R.choice(COS)
        city = R.choice(CITIES)
        jid = R.randint(3_000_000_000, 3_999_999_999)
        url = f"https://www.linkedin.com/jobs/view/{jid}"
        cards.append(
            f'<div class="job-card"><a class="title" href="{url}">{title}</a>'
            f'<p class="company">{co}</p><p class="location">{city}</p>'
            f'<p class="seniority">{sen}</p><p class="type">{typ}</p>'
            f'<p class="skills">{skills}</p><p class="notes">{notes}</p></div><!--/job-->')
        plain.append(f"{title}\n{co}\n{city}\n{sen} · {typ}\nSkills: {skills}\n{notes}\nView job: {url}\n")
        jobs_meta.append((title, co, fit))
    t0 = picks[0][0]
    subj = R.choice(ALERT_SUBJ).format(t=t0, k=len(picks), co=jobs_meta[0][1])
    html = ("<html><body><h2>Jobs matching your alert</h2>" + "".join(cards) +
            '<p class="footer">Manage job alerts · Unsubscribe · LinkedIn Corporation</p></body></html>')
    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["Subject"] = subj
    msg.set_content("Jobs matching your alert\n\n" + "\n".join(plain) + "\nManage job alerts · Unsubscribe")
    msg.add_alternative(html, subtype="html")
    meta = {"category": "job_alert", "action": "read_later", "jobs": jobs_meta}
    return msg, meta


def tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    return out


SENSITIVE_IN_INBOX = [
    ("Chase <no-reply@chase.com>", "Your statement is ready", "Your October statement for account ending 4421 is ready. "
     "New balance: $1,284.11. Minimum payment due Nov 25.", "statement_oct.pdf"),
    ("Google <no-reply@accounts.google.example>", "Your verification code", "Your Google verification code is 482913. "
     "Don't share it with anyone.", None),
    ("GitHub <noreply@github.com>", "[GitHub] Please reset your password", "We heard you need a password reset. Use the "
     "link below to choose a new password: https://github.com/password_reset/abc123token", None),
    ("Venmo <venmo@venmo.com>", "You paid Jordan $24.00", "You paid Jordan Lee $24.00 for dinner. Balance: $310.50.", None),
    ("Payroll <payroll@example.edu>", "Your W-2 is available", "Your 2025 Form W-2 is attached. SSN on file: 123-45-6789.",
     "W-2_2025.pdf"),
    ("Health Portal <portal@clinic.example>", "New lab results in your patient portal",
     "Your recent lab results are available in the patient portal. Log in to view your test results.", None),
]


def main() -> None:
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    mbox_path = config.RAW_DIR / "sample.mbox"
    if mbox_path.exists():
        mbox_path.unlink()
    box = mailbox.mbox(str(mbox_path))
    labels, job_gold, i = [], [], 0

    plan = [c for c, n in COUNTS.items() for _ in range(n)] + ["linkedin"] * 9 + ["sensitive"] * len(SENSITIVE_IN_INBOX)
    R.shuffle(plan)
    s_idx, jd_done = 0, False
    for kind in plan:
        i += 1
        mid = make_msgid(idstring=f"s{i:03d}", domain="sample.inbox-predator")
        if kind == "linkedin":
            msg, meta = linkedin_alert(i, job_gold)
        elif kind == "sensitive":
            frm, subj, body, att = SENSITIVE_IN_INBOX[s_idx]
            s_idx += 1
            msg = EmailMessage()
            msg["From"], msg["Subject"] = frm, subj
            msg.set_content(body)
            if att:
                msg.add_attachment(tiny_pdf("Confidential document"), maintype="application", subtype="pdf",
                                   filename=att)
            meta = None
        else:
            action, frm, subj, body = R.choice(T[kind])
            ctx = {"n": name(), "last": R.choice(LAST), "co": R.choice(COS), "co2": R.choice(COS),
                   "co3": R.choice(COS), "day": "Friday", "u": R.choice(FIRST).lower()}
            ctx["co_d"] = ctx["co"].lower().replace(" ", "") + ".example"
            msg = EmailMessage()
            msg["From"], msg["Subject"] = frm.format(**ctx), subj.format(**ctx)
            charset = "iso-8859-1" if i % 17 == 0 else "utf-8"
            text = body.format(**ctx) + ("\n\nMerci, à bientôt." if charset != "utf-8" else "")
            msg.set_content(text, charset=charset)
            if kind == "important" and not jd_done:
                jd_done = True
                msg.add_attachment(b"Job description: ML intern. Python, PyTorch. Summer 2027.",
                                   maintype="text", subtype="plain", filename="job_description.txt")
            meta = {"category": kind, "action": action}
        msg["To"] = ME
        msg["Date"] = format_datetime(when(i))
        msg["Message-ID"] = mid
        box.add(msg)
        if meta:
            meta["mid"] = mid
            labels.append(meta)
    box.flush()
    box.close()

    from src.loader import message_id_to_id
    with open(config.LABELS_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "action"])
        for m in labels:
            w.writerow([message_id_to_id(m["mid"]), m["category"], m["action"]])
    with open(config.JOB_GOLD_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["email_id", "job_title", "company", "my_rank_or_fit"])
        for m in labels:
            for title, co, fit in m.get("jobs", []):
                w.writerow([message_id_to_id(m["mid"]), title, co, fit])
    make_sensitive_bench()
    print(f"wrote {mbox_path} ({len(plan)} msgs), {len(labels)} labels, "
          f"{sum(len(m.get('jobs', [])) for m in labels)} gold jobs, {config.SENSITIVE_BENCH_PATH.name}")


def make_sensitive_bench() -> None:
    rs = random.Random(7)

    def cc() -> str:  # Luhn-valid 16-digit test card
        digits = [4] + [rs.randint(0, 9) for _ in range(14)]
        s = 0
        for k, d in enumerate(reversed(digits)):
            d2 = d * 2 if k % 2 == 0 else d
            s += d2 - 9 if d2 > 9 else d2
        digits.append((10 - s % 10) % 10)
        return " ".join("".join(map(str, digits[j:j + 4])) for j in range(0, 16, 4))

    filler = ("In this week's issue we cover product updates, community highlights, a long read about distributed "
              "systems, tips for staying productive, upcoming meetups and a roundup of reader questions. ") * 6
    sens = []
    gens = [
        ("otp_or_code", lambda: (f"Security <no-reply@{rs.choice(['okta','slack','discord','uber'])}.example>",
                                 rs.choice(["Your login code", "Your one-time passcode", "Sign-in attempt"]),
                                 f"Your verification code is {rs.randint(100000, 999999)}. It expires in 10 minutes.")),
        ("otp_or_code", lambda: ("Community Weekly <digest@forum.example>", "This week in the community",
                                 filler[:900] + f" P.S. your code is {rs.randint(1000, 9999)} to confirm your email. "
                                 + filler[:900])),  # adversarial: OTP inside a long newsletter
        ("bank_or_card_statement", lambda: (f"Statements <alerts@{rs.choice(['chase.com','citi.com','discover.com'])}>",
                                            "Your monthly statement is available",
                                            f"Account ending {rs.randint(1000, 9999)}. New balance ${rs.randint(100, 9000)}.{rs.randint(10, 99)}. "
                                            "Minimum payment due on the 25th.")),
        ("bank_or_card_statement", lambda: ("Billing <billing@my-utility-co.example>", "Account summary for November",
                                            f"Card {cc()} was charged. Routing number 021000021, account number "
                                            f"{rs.randint(10**9, 10**10 - 1)}. Available credit $2,000.")),  # non-bank domain
        ("credentials_or_reset_link", lambda: (f"{rs.choice(['Dropbox','Zoom','Canvas'])} <no-reply@svc.example>",
                                               "Reset your password",
                                               "Click this link to reset your password: https://svc.example/reset?t="
                                               f"{rs.getrandbits(64):x}. Your temporary password is Tq7!xk2p.")),
        ("credentials_or_reset_link", lambda: ("IT Helpdesk <it@example.edu>", "Your new account credentials",
                                               f"Username: alex{rs.randint(1, 99)} Password: Spring{rs.randint(10, 99)}! "
                                               "Please change it after first login.")),
        ("tax_or_government_id", lambda: ("Payroll <payroll@example.edu>", "Your 1099 tax form",
                                          f"Your 1099-NEC is ready. SSN: {rs.randint(100, 899)}-{rs.randint(10, 99)}-"
                                          f"{rs.randint(1000, 9999)}.")),
        ("tax_or_government_id", lambda: ("DMV <noreply@dmv.example>", "License renewal",
                                          f"Your driver's license number D{rs.randint(1000000, 9999999)} renewal is "
                                          "complete. Passport number on file updated.")),
        ("medical", lambda: ("Patient Portal <portal@hospital.example>", "New test results",
                             "Your lab results and diagnosis notes from Dr. Rao are in the patient portal. "
                             "Prescription refill approved.")),
        ("medical", lambda: ("Clinic <care@clinic.example>", "Appointment follow-up",
                             "Following your visit, your medical record has been updated with the new diagnosis.")),
    ]
    for k in range(60):
        cls, g = gens[k % len(gens)]
        frm, subj, body = g()
        sens.append({"id": f"sens_{k:03d}", "from": frm, "subject": subj, "body_text": body, "attachments": [],
                     "sensitive": True, "class": cls})
    benign_t = [
        ("Prof. Kim <kim@example.edu>", "Office hours moved", "Office hours move to Thursday 3pm this week."),
        ("GitHub <notifications@github.com>", "[repo] New issue #42", "A new issue was opened: flaky test in CI."),
        ("Recruiter <talent@nimbus.example>", "Interview scheduling",
         "Can you do a 45 minute call on Tuesday? Room code for the video call is 4512."),  # adversarial benign number
        ("Newsletter <digest@ai.example>", "Weekly AI digest", filler[:1200]),
        ("Amazon <ship@amazon.example>", "Order #112-4432 shipped", "Your order 112-4432 will arrive Friday."),
        ("Team <team@hack.example>", "Hackathon schedule", "Check-in at 9am, demos at 5pm, table number 17."),
        ("Coursera <promo@coursera.example>", "Your course statement of accomplishment",
         "Congrats! Your statement of accomplishment for Machine Learning is ready to share."),  # 'statement' benign
        ("Mom <mom@family.example>", "Dinner Sunday?", "Are you coming for dinner on Sunday? Bring the 2 books."),
        ("Landlord <office@apts.example>", "Maintenance on Nov 20", "Water will be shut off from 10am to 2pm."),
        ("Kaggle <noreply@kaggle.example>", "Competition update", "Leaderboard refreshed; you are ranked 1204."),
    ]
    benign = []
    for k in range(60):
        frm, subj, body = benign_t[k % len(benign_t)]
        benign.append({"id": f"benign_{k:03d}", "from": frm, "subject": subj, "body_text": body + f" (ref {k})",
                       "attachments": [], "sensitive": False, "class": "none"})
    with open(config.SENSITIVE_BENCH_PATH, "w") as f:
        for row in sens + benign:
            f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
