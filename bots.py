"""
bots.py - Bot (agent) profile generation for AI Social Bot Simulator.

Generates 1 000 realistic social-media profiles and caches them to disk so
the same identities survive across runs.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

from config import cfg

logger = logging.getLogger(__name__)

# ── Static data pools ─────────────────────────────────────────────────────────

FIRST_NAMES = [
    "Arjun", "Priya", "Rohan", "Ananya", "Kiran", "Sneha", "Vikram", "Divya",
    "Aditya", "Meera", "Rahul", "Pooja", "Siddharth", "Kavya", "Amit", "Nisha",
    "James", "Emily", "Michael", "Sarah", "David", "Jessica", "Daniel", "Ashley",
    "Liam", "Olivia", "Noah", "Emma", "William", "Ava", "Lucas", "Sophia",
    "Chen", "Wei", "Yuki", "Haruto", "Min", "Ji-ho", "Fatima", "Omar",
    "Isabella", "Ethan", "Mia", "Alexander", "Chloe", "Sebastian", "Lily",
    "Benjamin", "Eleanor", "Mason", "Zoe", "Logan", "Hannah", "Jackson", "Aria",
    "Aiden", "Scarlett", "Elijah", "Grace", "Carter", "Nora", "Luke", "Layla",
    "Dylan", "Riley", "Owen", "Penelope", "Ryan", "Zoey", "Nathan", "Leah",
    "Ravi", "Anjali", "Suresh", "Lakshmi", "Vijay", "Sunita", "Raj", "Deepa",
    "Carlos", "Maria", "Juan", "Sofia", "Pedro", "Ana", "Miguel", "Elena",
    "Kofi", "Amara", "Kwame", "Zainab", "Tariq", "Nadia", "Hassan", "Leila",
]

LAST_NAMES = [
    "Sharma", "Patel", "Kumar", "Singh", "Gupta", "Mehta", "Joshi", "Verma",
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Jackson", "White",
    "Harris", "Martin", "Thompson", "Robinson", "Walker", "Young", "Hall",
    "Chen", "Wang", "Liu", "Zhang", "Li", "Yang", "Wu", "Zhou", "Lin",
    "Tanaka", "Suzuki", "Sato", "Watanabe", "Yamamoto", "Nakamura", "Kobayashi",
    "Kim", "Park", "Lee", "Choi", "Jung", "Kang", "Cho", "Yoon",
    "Ahmed", "Hassan", "Ali", "Omar", "Khan", "Malik", "Rahman", "Hussain",
    "Okonkwo", "Mensah", "Diallo", "Mbeki", "Nkosi", "Osei", "Asante",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Perez",
    "Rivera", "Torres", "Ramirez", "Flores", "Reyes", "Cruz", "Morales",
    "Reed", "Cook", "Bell", "Murphy", "Bailey", "Cooper", "Richardson",
]

LOCATIONS = [
    "San Francisco, CA", "New York, NY", "Austin, TX", "Seattle, WA",
    "Boston, MA", "Chicago, IL", "Los Angeles, CA", "Denver, CO",
    "London, UK", "Berlin, Germany", "Paris, France", "Amsterdam, Netherlands",
    "Toronto, Canada", "Vancouver, Canada", "Sydney, Australia", "Melbourne, Australia",
    "Bangalore, India", "Mumbai, India", "Hyderabad, India", "Delhi, India",
    "Singapore", "Tokyo, Japan", "Seoul, South Korea", "Shanghai, China",
    "Dubai, UAE", "Tel Aviv, Israel", "Stockholm, Sweden", "Zurich, Switzerland",
    "São Paulo, Brazil", "Buenos Aires, Argentina", "Lagos, Nigeria", "Nairobi, Kenya",
]

SUBHEADINGS = [
    "Software Engineer @ {company}", "Senior Developer | {skill} Enthusiast",
    "Founder & CTO at {company}", "Full-Stack Engineer | Building the future",
    "ML Engineer | Turning data into decisions", "DevOps Lead @ {company}",
    "Product Engineer | {skill} | Open Source Contributor",
    "Backend Architect | Distributed Systems", "AI Researcher | {skill} | Speaker",
    "Principal Engineer @ {company} | {skill}",
    "Tech Lead | Mentoring developers worldwide",
    "Senior Data Scientist | {skill} | PhD Candidate",
    "Cloud Architect | AWS & GCP | {skill}",
    "Mobile Engineer | React Native & Flutter",
    "Security Engineer | Ethical Hacker | Speaker",
    "Staff Engineer @ {company} | Platform",
    "Engineering Manager | Team builder | {skill}",
    "Freelance Developer | {skill} | Remote",
    "Startup CTO | Previously @ {company}",
    "Open Source Maintainer | {skill} Core Team",
]

COMPANIES = [
    "Google", "Microsoft", "Amazon", "Meta", "Apple", "Netflix", "Spotify",
    "Stripe", "Airbnb", "Uber", "Lyft", "Slack", "Figma", "Notion",
    "Vercel", "Supabase", "PlanetScale", "Fly.io", "Railway", "Render",
    "HashiCorp", "Datadog", "Snowflake", "Databricks", "Palantir",
    "Anthropic", "OpenAI", "Hugging Face", "Cohere", "Mistral AI",
    "TechCorp", "InnoSoft", "DevStudio", "CloudByte", "PixelForge",
]

SKILLS_POOL = [
    "Python", "TypeScript", "Rust", "Go", "Java", "Kotlin", "Swift", "C++",
    "React", "Next.js", "Vue.js", "Svelte", "Angular", "Node.js", "FastAPI",
    "GraphQL", "gRPC", "REST APIs", "Microservices", "Event-Driven Architecture",
    "Machine Learning", "Deep Learning", "NLP", "Computer Vision", "LLMOps",
    "PyTorch", "TensorFlow", "Scikit-learn", "Hugging Face Transformers",
    "AWS", "GCP", "Azure", "Kubernetes", "Docker", "Terraform", "Pulumi",
    "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "Kafka", "RabbitMQ",
    "CI/CD", "GitHub Actions", "ArgoCD", "Prometheus", "Grafana",
    "System Design", "Distributed Systems", "Database Architecture",
    "Cybersecurity", "Penetration Testing", "Cryptography", "Zero Trust",
    "React Native", "Flutter", "iOS Development", "Android Development",
    "Blockchain", "Smart Contracts", "Solidity", "Web3",
    "Data Engineering", "Spark", "dbt", "Airflow", "Data Pipelines",
]

EDUCATION_POOL = [
    ("Computer Science", ["MIT", "Stanford", "CMU", "UC Berkeley", "Caltech"]),
    ("Software Engineering", ["Georgia Tech", "University of Michigan", "Purdue", "UIUC"]),
    ("Data Science", ["Columbia", "NYU", "University of Washington", "Boston University"]),
    ("Electrical Engineering", ["ETH Zurich", "Imperial College", "TU Delft", "NTU Singapore"]),
    ("Mathematics", ["Oxford", "Cambridge", "Princeton", "Yale"]),
    ("Information Systems", ["IIT Bombay", "IIT Delhi", "NUS", "KAIST"]),
]

PROJECTS_POOL = [
    "Building an open-source {skill} framework for faster API development",
    "Developing a real-time collaborative coding platform with {skill}",
    "Creating an AI-powered code review tool using {skill} and LLMs",
    "Designing a distributed caching layer with {skill} for high-throughput systems",
    "Building a personal finance dashboard with {skill} and predictive analytics",
    "Launching a developer productivity tool that integrates {skill} workflows",
    "Researching novel approaches to model compression using {skill}",
    "Creating a multi-tenant SaaS platform with {skill} at its core",
    "Building a real-time anomaly detection system with {skill} and streaming data",
    "Developing a cross-platform mobile app with {skill} for health tracking",
    "Architecting a zero-downtime deployment pipeline using {skill}",
    "Building a vector search engine powered by {skill} for semantic retrieval",
    "Creating an educational platform that teaches {skill} interactively",
    "Designing a high-availability {skill} infrastructure for fintech",
    "Building open-source tooling to simplify {skill} adoption for teams",
]

ABOUT_TEMPLATES = [
    (
        "Passionate {role} with {years}+ years of experience building scalable systems. "
        "Deep expertise in {skill1} and {skill2}. "
        "I love open source, developer communities, and turning complex problems into elegant solutions."
    ),
    (
        "I build things at the intersection of {skill1} and {skill2}. "
        "{years} years in the industry, currently focused on {project_area}. "
        "I write about tech, share learnings, and mentor early-career engineers."
    ),
    (
        "Senior engineer specializing in {skill1}. "
        "Previously built systems that served millions of users. "
        "Passionate about {skill2}, clean architecture, and developer experience."
    ),
    (
        "Engineering lead with {years} years of experience across startups and big tech. "
        "My work spans {skill1}, {skill2}, and everything in between. "
        "Always learning, always shipping."
    ),
    (
        "I'm a {role} who cares deeply about {skill1} and its real-world impact. "
        "Open-source contributor. Speaker at local meetups. "
        "Currently exploring {skill2} and what it means for the next decade of software."
    ),
]

ROLES = [
    "software engineer", "backend developer", "full-stack engineer",
    "ML engineer", "data scientist", "cloud architect", "DevOps engineer",
    "platform engineer", "mobile developer", "security researcher",
    "engineering manager", "tech lead", "staff engineer", "principal engineer",
]

EXPERIENCE_TITLES = [
    "Senior Software Engineer", "Staff Engineer", "Lead Developer",
    "Principal Engineer", "Software Architect", "Engineering Manager",
    "Tech Lead", "Backend Engineer", "Full-Stack Developer", "ML Engineer",
    "DevOps Engineer", "Cloud Engineer", "Data Engineer", "Platform Engineer",
]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Education:
    degree: str
    field: str
    institution: str
    year_start: int
    year_end: int


@dataclass
class Experience:
    title: str
    company: str
    duration: str
    description: str


@dataclass
class BotProfile:
    bot_id: str
    name: str
    subheading: str
    location: str
    about: str
    education: List[dict]
    skills: List[str]
    experience: List[dict]
    project: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BotProfile":
        return cls(**d)


# ── Generator ─────────────────────────────────────────────────────────────────

def _pick(lst: list):
    return random.choice(lst)


def _sample(lst: list, k: int) -> list:
    return random.sample(lst, min(k, len(lst)))


def generate_bot(index: int) -> BotProfile:
    """Create a single realistic bot profile deterministically from index."""
    rng = random.Random(index)          # seeded so same index → same bot

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    name = f"{first} {last}"

    company = rng.choice(COMPANIES)
    skills = rng.sample(SKILLS_POOL, k=rng.randint(5, 10))
    primary_skill = skills[0]

    subheading = rng.choice(SUBHEADINGS).format(company=company, skill=primary_skill)
    location = rng.choice(LOCATIONS)

    years = rng.randint(2, 18)
    role = rng.choice(ROLES)
    skill1, skill2 = skills[0], skills[1] if len(skills) > 1 else skills[0]
    project_area = rng.choice(["AI/ML", "platform engineering", "developer tools",
                                "cloud infrastructure", "real-time systems"])
    about = rng.choice(ABOUT_TEMPLATES).format(
        role=role, years=years, skill1=skill1, skill2=skill2, project_area=project_area
    )

    # Education
    field_name, schools = rng.choice(EDUCATION_POOL)
    grad_year = rng.randint(2005, 2021)
    edu = Education(
        degree=rng.choice(["B.S.", "B.Tech", "M.S.", "M.Eng", "Ph.D."]),
        field=field_name,
        institution=rng.choice(schools),
        year_start=grad_year - rng.randint(3, 5),
        year_end=grad_year,
    )

    # Experience (1-3 positions)
    experiences = []
    for _ in range(rng.randint(1, 3)):
        exp_years = rng.randint(1, 5)
        title = rng.choice(EXPERIENCE_TITLES)
        exp_company = rng.choice(COMPANIES)
        exp = Experience(
            title=title,
            company=exp_company,
            duration=f"{exp_years} yr{'s' if exp_years > 1 else ''}",
            description=(
                f"Built and maintained {rng.choice(skills)}-based systems serving "
                f"{rng.randint(10, 500)}K+ users. Led a team of {rng.randint(2, 12)} engineers."
            ),
        )
        experiences.append(asdict(exp))

    # Project
    project = rng.choice(PROJECTS_POOL).format(skill=primary_skill)

    return BotProfile(
        bot_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"bot-{index}")),
        name=name,
        subheading=subheading,
        location=location,
        about=about,
        education=[asdict(edu)],
        skills=skills,
        experience=experiences,
        project=project,
    )


def generate_all_bots(n: int = cfg.TOTAL_BOTS) -> List[BotProfile]:
    """Generate n bot profiles (or load from cache)."""
    cache_path = Path(cfg.BOTS_FILE)

    if cache_path.exists():
        logger.info("Loading %d bots from cache: %s", n, cache_path)
        with cache_path.open() as f:
            raw = json.load(f)
        # Only return as many as requested
        return [BotProfile.from_dict(d) for d in raw[:n]]

    logger.info("Generating %d bot profiles…", n)
    bots = [generate_bot(i) for i in range(n)]

    with cache_path.open("w") as f:
        json.dump([b.to_dict() for b in bots], f, indent=2)
    logger.info("Bot profiles saved to %s", cache_path)

    return bots
