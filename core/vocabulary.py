"""
The skill vocabulary — what the deterministic extractor can recognise.

WHY THIS IS A SEPARATE FILE
---------------------------
It shipped with fifteen entries, all product-management: SQL, Figma, Jira,
Roadmapping, Tableau and so on. That was fine while the only test data was a PM
resume, and quietly wrong for everyone else.

A backend engineer's job advert asking for "Java, Spring Boot, Kubernetes and
Kafka" parsed down to nothing. Java was never recognised as a requirement, so
it could never be reported as missing, and the fit score was computed against a
fraction of what the role actually asked for. The user sees a confident report
with no indication that most of the posting was invisible to it.

That failure is silent, which makes it worse than a crash.

WHAT THIS IS AND ISN'T
----------------------
This is the COLD-CACHE path. Once LLM extraction has parsed a posting, the
model's structured output is used instead and this vocabulary doesn't matter.
But every user's first report runs on this, and "your first report was wrong"
is not recoverable.

It is deliberately not exhaustive. A vocabulary of ten thousand terms would
match fragments everywhere and produce noise. Each entry has to be a thing an
advert genuinely lists as a requirement, and a thing a person would put on a
resume.

ALIASES MATTER MORE THAN ENTRIES
--------------------------------
"Postgres", "PostgreSQL" and "psql" are one skill. Without canonicalisation
they become three underpowered hypotheses and none survives correction — the
same skill splitting its own evidence three ways.
"""

from __future__ import annotations

# (canonical, [aliases]) — grouped by domain for maintenance, flat at runtime.
# Aliases are matched case-insensitively on word boundaries.

PROGRAMMING = [
    ("Python", ["python", "py"]),
    ("Java", ["java", "core java", "java 8", "java 11", "java 17"]),
    ("JavaScript", ["javascript", "js", "es6", "ecmascript"]),
    ("TypeScript", ["typescript", "ts"]),
    ("Go", ["golang", "go lang", "go developer", "go programming"]),
    ("C++", ["c++", "cpp"]),
    ("C#", ["c#", "csharp", ".net", "dotnet"]),
    ("Ruby", ["ruby", "ruby on rails", "rails"]),
    ("PHP", ["php", "laravel"]),
    ("Rust", ["rust"]),
    ("Kotlin", ["kotlin"]),
    ("Swift", ["swift", "swiftui"]),
    ("Scala", ["scala"]),
    ("R", ["r programming", "rstudio", "r language", " r,", " r "]),
]

BACKEND = [
    ("Spring", ["spring", "spring boot", "springboot"]),
    ("Node.js", ["node.js", "nodejs", "node", "express.js", "expressjs"]),
    ("Django", ["django"]),
    ("Flask", ["flask", "fastapi"]),
    ("API Design", ["api design", "rest api", "restful", "graphql", "grpc",
                    "api development"]),
    ("Microservices", ["microservices", "microservice architecture"]),
    ("System Design", ["system design", "distributed systems", "architecture design",
                       "scalable systems"]),
]

FRONTEND = [
    ("React", ["react", "react.js", "reactjs", "next.js", "nextjs"]),
    ("Angular", ["angular", "angularjs"]),
    ("Vue", ["vue", "vue.js", "vuejs", "nuxt"]),
    ("HTML/CSS", ["html", "css", "html5", "css3", "scss", "sass", "tailwind"]),
    ("Accessibility", ["accessibility", "wcag", "a11y", "screen reader"]),
]

DATA = [
    ("SQL", ["sql", "postgresql", "postgres", "mysql", "t-sql", "psql",
             "sql server", "sqlite"]),
    ("NoSQL", ["nosql", "mongodb", "mongo", "dynamodb", "cassandra"]),
    ("Data Analysis", ["data analysis", "data analytics", "analytics",
                       "quantitative analysis"]),
    ("Excel", ["excel", "spreadsheets", "google sheets", "advanced excel"]),
    ("Tableau", ["tableau"]),
    ("Power BI", ["power bi", "powerbi"]),
    ("Looker", ["looker", "lookml"]),
    ("dbt", ["dbt", "data build tool"]),
    ("Snowflake", ["snowflake"]),
    ("Spark", ["spark", "pyspark", "apache spark"]),
    ("Airflow", ["airflow", "apache airflow", "dagster", "prefect"]),
    ("ETL", ["etl", "elt", "data pipeline", "data pipelines", "data engineering"]),
    ("Pandas", ["pandas", "numpy"]),
    ("Statistics", ["statistics", "statistical analysis", "statistical modelling",
                    "statistical modeling"]),
    ("Machine Learning", ["machine learning", "ml", "deep learning",
                          "neural networks", "pytorch", "tensorflow", "scikit-learn"]),
    ("Data Warehousing", ["data warehouse", "data warehousing", "bigquery", "redshift"]),
]

INFRA = [
    ("AWS", ["aws", "amazon web services", "ec2", "s3 bucket", "lambda"]),
    ("Azure", ["azure", "microsoft azure"]),
    ("GCP", ["gcp", "google cloud", "google cloud platform"]),
    ("Docker", ["docker", "containerisation", "containerization", "containers"]),
    ("Kubernetes", ["kubernetes", "k8s", "eks", "gke"]),
    ("Terraform", ["terraform", "infrastructure as code", "iac", "pulumi"]),
    ("CI/CD", ["ci/cd", "cicd", "continuous integration", "continuous deployment",
               "jenkins", "github actions", "gitlab ci"]),
    ("Linux", ["linux", "unix", "bash", "shell scripting"]),
    ("Kafka", ["kafka", "apache kafka", "event streaming", "rabbitmq"]),
    ("Monitoring", ["monitoring", "observability", "datadog", "prometheus",
                    "grafana", "new relic"]),
    ("Git", ["github", "gitlab", "version control", "bitbucket", "git workflow"]),
]

SECURITY = [
    ("Security", ["security", "cybersecurity", "infosec", "application security"]),
    ("Penetration Testing", ["penetration testing", "pen testing", "pentest",
                             "ethical hacking"]),
    ("Compliance", ["compliance", "soc 2", "iso 27001", "gdpr", "hipaa", "pci dss"]),
    ("Networking", ["networking", "tcp/ip", "dns", "vpn", "firewall", "ccna"]),
]

TESTING = [
    ("Automated Testing", ["automated testing", "test automation", "selenium",
                           "cypress", "playwright", "unit testing", "pytest",
                           "junit", "jest"]),
    ("QA", ["qa", "quality assurance", "manual testing", "test cases"]),
]

PRODUCT = [
    ("Roadmapping", ["roadmap", "roadmapping", "product roadmap", "prioritisation",
                     "prioritization"]),
    ("A/B Testing", ["a/b testing", "ab testing", "split testing", "experimentation"]),
    ("User Research", ["user research", "customer research", "user interviews",
                       "usability testing", "discovery"]),
    ("Stakeholder Management", ["stakeholder management", "stakeholder",
                                "cross-functional", "cross functional"]),
    ("Agile", ["agile", "scrum", "kanban", "sprint planning", "safe"]),
    ("Jira", ["jira", "atlassian", "confluence", "linear", "asana"]),
    ("Amplitude", ["amplitude"]),
    ("Mixpanel", ["mixpanel"]),
    ("Google Analytics", ["google analytics", "ga4"]),
    ("Product Strategy", ["product strategy", "product vision", "go-to-market",
                          "gtm"]),
]

DESIGN = [
    ("Figma", ["figma", "sketch", "adobe xd"]),
    ("UI Design", ["ui design", "visual design", "interface design"]),
    ("UX Design", ["ux design", "user experience", "interaction design"]),
    ("Prototyping", ["prototyping", "wireframing", "wireframes", "prototypes"]),
    ("Design Systems", ["design system", "design systems", "component library",
                        "style guide"]),
    ("Adobe Creative Suite", ["photoshop", "illustrator", "indesign",
                              "adobe creative"]),
]

MARKETING = [
    ("SEO", ["seo", "search engine optimisation", "search engine optimization"]),
    ("Paid Advertising", ["paid ads", "google ads", "ppc", "paid media",
                          "meta ads", "facebook ads", "performance marketing"]),
    ("Content Marketing", ["content marketing", "content strategy", "copywriting",
                           "content creation"]),
    ("Email Marketing", ["email marketing", "mailchimp", "klaviyo", "crm campaigns"]),
    ("Social Media", ["social media", "community management", "social strategy"]),
    ("Brand", ["brand strategy", "branding", "brand management"]),
    ("Marketing Automation", ["marketing automation", "hubspot", "marketo",
                              "pardot"]),
]

SALES = [
    ("Salesforce", ["salesforce", "sfdc"]),
    ("CRM", ["crm", "hubspot crm", "pipedrive", "zoho"]),
    ("Lead Generation", ["lead generation", "lead gen", "prospecting",
                         "outbound", "cold calling"]),
    ("Account Management", ["account management", "client management",
                            "customer success", "relationship management"]),
    ("Negotiation", ["negotiation", "closing", "contract negotiation"]),
]

FINANCE = [
    ("Financial Modelling", ["financial modelling", "financial modeling",
                             "forecasting", "budgeting", "fp&a"]),
    ("Accounting", ["accounting", "bookkeeping", "gaap", "ifrs", "reconciliation"]),
    ("Audit", ["auditing", "internal audit", "sox compliance", "audit experience"]),
    ("Tax", ["taxation", "gst filing", "vat returns", "tax compliance"]),
    ("ERP", ["erp", "sap", "oracle financials", "netsuite", "tally"]),
]

OPERATIONS = [
    ("Project Management", ["project management", "pmp", "prince2",
                            "programme management", "program management"]),
    ("Process Improvement", ["process improvement", "lean", "six sigma",
                             "operational excellence"]),
    ("Supply Chain", ["supply chain", "logistics", "procurement", "inventory"]),
    ("Vendor Management", ["vendor management", "supplier management"]),
]

PEOPLE = [
    # "hiring" and "sourcing" are removed deliberately: every job advert on
    # earth contains the word "hiring", so as an alias it fired on 96 of 96
    # postings and presented itself as the top skill gap for a backend
    # engineer. An alias has to be specific to the SKILL, not to the genre of
    # document the skill appears in.
    ("Recruiting", ["recruiting", "recruitment", "talent acquisition",
                    "candidate sourcing", "full-cycle recruiting"]),
    ("HR", ["hr", "human resources", "hris", "workday", "people operations"]),
    ("Onboarding", ["onboarding", "employee onboarding"]),
    ("Performance Management", ["performance management", "performance reviews"]),
]

HEALTHCARE = [
    ("Patient Care", ["patient care", "clinical care", "bedside"]),
    ("Clinical Documentation", ["clinical documentation", "emr", "ehr", "epic",
                                "cerner", "charting"]),
    ("Medical Coding", ["medical coding", "icd-10", "cpt coding"]),
    ("Regulatory Affairs", ["regulatory affairs", "fda", "clinical trials",
                            "gcp compliance"]),
]

SOFT = [
    ("Communication", ["written communication", "verbal communication",
                       "presentation skills", "public speaking"]),
    ("Leadership", ["leadership", "team leadership", "people management",
                    "line management", "mentoring"]),
    ("Problem Solving", ["problem solving", "analytical thinking",
                         "critical thinking"]),
    ("Customer Service", ["customer service", "customer support", "client service",
                          "helpdesk"]),
]

DOMAINS: dict[str, list[tuple[str, list[str]]]] = {
    "programming": PROGRAMMING,
    "backend": BACKEND,
    "frontend": FRONTEND,
    "data": DATA,
    "infra": INFRA,
    "security": SECURITY,
    "testing": TESTING,
    "product": PRODUCT,
    "design": DESIGN,
    "marketing": MARKETING,
    "sales": SALES,
    "finance": FINANCE,
    "operations": OPERATIONS,
    "people": PEOPLE,
    "healthcare": HEALTHCARE,
    "soft": SOFT,
}


def all_entries() -> list[tuple[str, list[str]]]:
    """Every entry, flat. Order is stable so extraction is reproducible."""
    out: list[tuple[str, list[str]]] = []
    for group in DOMAINS.values():
        out.extend(group)
    return out


def stats() -> dict:
    entries = all_entries()
    return {
        "skills": len(entries),
        "aliases": sum(len(a) for _, a in entries),
        "domains": len(DOMAINS),
        "by_domain": {k: len(v) for k, v in DOMAINS.items()},
    }
