# -*- coding: utf-8 -*-
"""
study_syllabus.py — G.I.L. built-in syllabi.

Authoritative course material for known exams. When the user asks GIL to
prepare them for one of these, study_coach grounds the generated course in the
REAL syllabus below (not just web research), and generates in the right
language (Hebrew for the Israeli intelligence tests).

Add more exams by adding entries to SYLLABI.
"""

# Israeli military intelligence selection tests — user-supplied syllabus.
_IL_INTEL_CYBER = """\
מבחני סייבר (Cyber) ונתיב הנדסת תוכנה — הרקע של הלומד הוא Java (OOP חזק).

יסודות התכנות (Java ושפות אחרות):
- משתנים וסוגי נתונים: primitive types (int, double, boolean, char).
- מבני בקרה: תנאים (if-else), לולאות (for, while), ומבני בחירה (switch).
- מבני נתונים בסיסיים: מערכים, רשימות מקושרות (Linked Lists), מחסנית (Stack),
  תור (Queue), ומפות/טבלאות גיבוב (Hash Maps).
- יעילות וסיבוכיות: סיבוכיות זמן ומקום (Big O) — ההבדל בין חיפוש ליניארי לבינארי.

תכנות מונחה עצמים (OOP — החוזק של הלומד):
- ירושה, פולימורפיזם, כמוס (Encapsulation), ומופשטות (Abstraction).
- מחלקות וממשקים: כתיבת קלאסים, abstract, מימוש interface, דריסת מתודות (@Override).

מעבר מ-Java לשפות אחרות (חובה בסיס):
- שפת C (חשובה מאוד לסייבר): ניהול זיכרון ידני, מצביעים (Pointers),
  הקצאות זיכרון (malloc, free), וכתובות בזיכרון.
- שפת Python: סינטקס בסיסי (הזחות במקום סוגריים), Lists, Dicts, ו-Tuples.

רשתות ותקשורת:
- מודל OSI ומודל TCP/IP: השכבות (פיזית, רשת, תעבורה, אפליקציה).
- פרוטוקולים: TCP מול UDP (אמינות מול מהירות), IP, DNS, HTTP/HTTPS, ARP.
- מושגי יסוד: כתובות IP (IPv4/IPv6), מסכות רשת (Subnet Mask),
  פורטים נפוצים (80 ל-HTTP, 443 ל-HTTPS, 22 ל-SSH).

מערכות הפעלה וארכיטקטורה:
- Linux: פקודות טרמינל (Bash) — ls, cd, grep, chmod, cat.
- ארכיטקטורת מחשב: איך מעבד (CPU) עובד, תפקיד ה-RAM, מושגי אסמבלי בסיסיים
  (Registers, ה-Stack הפנימי של פונקציות).
"""

_IL_INTEL_SHACHIM = """\
מבחני שחקים וחבצלות — לא מניחים ידע מוקדם; בודקים פוטנציאל למידה מהיר תחת לחץ זמן.

מבחן שפה מומצאת (המפורסם ביותר):
- נותנים דף חוקים של שפה שאינה קיימת (דקדוק, הטיות, זמנים, מילים).
- הדרישה: לתרגם משפטים מהשפה המומצאת לעברית ולהיפך.
- הכנה: הבנה טובה של חלקי דיבר בעברית ובאנגלית, ותרגול חוקי דקדוק קשוחים.

מבחן "רובוט" / מכונת מצבים (חשיבה אלגוריתמית):
- תרשים של מערכת חוקים או "רובוט" שמקבל קלט ומשנה מצב (State Machine).
- הדרישה: לעקוב אחרי שרשרת פקודות ארוכה ולמצוא את המצב הסופי או הקלט ההתחלתי.
- רקע Java עוזר מאוד בחלק הלוגי.

מבחן מחקר וניתוח טקסט (רלוונטי לחבצלות ואח"מ):
- קטעי מידע ארוכים ומורכבים בנושאים זרים (היסטוריה, כלכלה, אסטרטגיה, מדע).
- הדרישה: הצלבת נתונים, הסקת מסקנות מהירה, זיהוי סתירות בין מקורות.

מתמטיקה גבוהה ועיבוד נתונים:
- הבנה גרפית, קריאת טבלאות מורכבות, וחישובים סטטיסטיים מהירים ללא מחשבון.
"""

_IL_INTEL_KLAL = """\
מבחני כלל חמ"ן (כלל מודיעין) — ממפים לעשרות מסלולים (טכנולוגי, תרגום, מחקר, הפקה).

חשיבה כמותית ומילולית:
- מתמטיקה: בעיות הספק, בעיות תנועה, אחוזים, הסתברות בסיסית, ומשוואות.
- אנלוגיות מילוליות: הבנת הקשר בין צמדי מילים והחלתו על צמד אחר.
- הבנת הנקרא: טקסטים קצרים ושאלות לוגיות מורכבות.

אנגלית ברמה גבוהה:
- השלמת משפטים, אוצר מילים מתקדם (מונחים טכניים/פוליטיים), הבנת הנקרא.

מטריצות וצורות (צורני):
- זיהוי חוקיות חבויה ברצף צורות (סיבובים, שינויי צבע, חיסור/הוספת קווים) — כמו IQ.

מבחן אישיות ואמינות (שאלון 300):
- מאות שאלות על האופי, חוזרות בניסוחים שונים, לבדיקת עקביות, לחץ, עבודת צוות ומוטיבציה.
"""

# key -> (display title, language, keyword triggers, syllabus text)
SYLLABI = [
    ("il_cyber", "מבחן סייבר ונתיב הנדסת תוכנה — מודיעין", "Hebrew",
     ("סייבר", "cyber", "הנדסת תוכנה", "software eng", "8200", "ממר"),
     _IL_INTEL_CYBER),
    ("il_shachim", "מבחני שחקים וחבצלות — מודיעין", "Hebrew",
     ("שחקים", "חבצלות", "shachim", "chavatzalot", "shakim", "invented language",
      "state machine", "שפה מומצאת"),
     _IL_INTEL_SHACHIM),
    ("il_klal", "מבחני כלל חמ\"ן (כלל מודיעין)", "Hebrew",
     ("כלל חמן", "כלל חמ", "כלל מודיעין", "klal chaman", "chaman", "חמן",
      "אנלוגיות", "שאלון 300", "מטריצות"),
     _IL_INTEL_KLAL),
]


def match(exam: str):
    """Return (title, language, syllabus_text) if the exam matches a built-in
    syllabus, else None."""
    low = (exam or "").lower()
    best = None
    for _key, title, lang, triggers, text in SYLLABI:
        if any(t.lower() in low for t in triggers):
            return (title, lang, text)
    return best


def all_titles() -> list:
    return [title for _k, title, *_ in SYLLABI]
