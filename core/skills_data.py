"""
The built-in skill vocabulary.

WHY THIS FILE EXISTS SEPARATELY
-------------------------------
The original vocabulary had fifteen entries, all product-management. A backend
job advert was parsed down to "SQL" — Java, Spring, Kubernetes and Kafka were
invisible, so they could never be reported as required, never appear as missing,
and never enter a fit score.

The failure is silent and asymmetric: the report still renders, still looks
confident, and is quietly wrong for everyone outside one job family. A product
whose entire claim is that it refuses to overclaim cannot compute fit against a
fraction of what a role actually asked for.

HOW TO EXTEND IT
----------------
Add a canonical name and its aliases. Aliases are matched case-insensitively on
word boundaries, so short ones are dangerous: "go" would match "going", "R"
would match every capital R. Those need the boundary-safe forms below.

This is still a stopgap. LLM extraction reads any advert without a vocabulary at
all, and the cache warms toward it. This list is what day-one users hit before
that happens, which is exactly why it can't stay fifteen words long.
"""

from __future__ import annotations


def load_default(v) -> None:
    """Populate a SkillVocab in place. Grouped by job family for maintenance."""

    # ---------------------------------------------------------------- data
    v.add("SQL", ["sql", "postgresql", "postgres", "mysql", "t-sql", "psql",
                  "sqlite", "mssql", "oracle sql"])
    v.add("Python", ["python", "py", "python3"])
    v.add("R", ["r language", "rstats", "r programming"])
    v.add("Excel", ["excel", "spreadsheets", "google sheets", "vlookup", "pivot tables"])
    v.add("Tableau", ["tableau"])
    v.add("Power BI", ["power bi", "powerbi"])
    v.add("Looker", ["looker", "lookml"])
    v.add("dbt", ["dbt", "data build tool"])
    v.add("Snowflake", ["snowflake"])
    v.add("BigQuery", ["bigquery", "big query"])
    v.add("Redshift", ["redshift"])
    v.add("Spark", ["spark", "pyspark", "apache spark"])
    v.add("Airflow", ["airflow", "apache airflow"])
    v.add("ETL", ["etl", "elt", "data pipeline", "data pipelines"])
    v.add("Data Modelling", ["data modelling", "data modeling", "dimensional modelling",
                             "star schema"])
    v.add("Statistics", ["statistics", "statistical analysis", "regression",
                         "hypothesis testing"])
    v.add("Pandas", ["pandas", "numpy"])

    # ------------------------------------------------------------ backend
    v.add("Java", ["java", "java 8", "java 11", "java 17", "core java"])
    v.add("Spring", ["spring", "spring boot", "springboot"])
    v.add("C#", ["c#", "csharp", ".net", "dotnet", "asp.net"])
    v.add("Go", ["golang", "go lang", "go programming"])   # never bare "go"
    v.add("Node.js", ["node.js", "nodejs", "node js", "express.js", "expressjs"])
    v.add("Ruby", ["ruby", "ruby on rails", "rails"])
    v.add("PHP", ["php", "laravel", "symfony"])
    v.add("C++", ["c++", "cpp"])
    v.add("Rust", ["rust lang", "rustlang"])
    v.add("Scala", ["scala"])
    v.add("Kotlin", ["kotlin"])
    v.add("API Design", ["api design", "rest api", "restful", "graphql", "grpc",
                         "openapi", "swagger"])
    v.add("Microservices", ["microservices", "micro-services", "service oriented"])
    v.add("System Design", ["system design", "distributed systems", "scalability",
                            "high availability"])
    v.add("Message Queues", ["kafka", "rabbitmq", "sqs", "pub/sub", "message queue"])
    v.add("Caching", ["redis", "memcached", "caching"])
    v.add("MongoDB", ["mongodb", "mongo", "nosql", "dynamodb", "cassandra"])

    # ----------------------------------------------------------- frontend
    v.add("JavaScript", ["javascript", "js", "es6", "ecmascript"])
    v.add("TypeScript", ["typescript", "ts"])
    v.add("React", ["react", "react.js", "reactjs", "next.js", "nextjs"])
    v.add("Vue", ["vue", "vue.js", "vuejs", "nuxt"])
    v.add("Angular", ["angular", "angularjs"])
    v.add("HTML/CSS", ["html", "css", "scss", "sass", "tailwind", "html5", "css3"])
    v.add("Accessibility", ["accessibility", "wcag", "a11y", "screen reader"])
    v.add("Responsive Design", ["responsive design", "mobile-first", "cross-browser"])

    # -------------------------------------------------------------- mobile
    v.add("Swift", ["swift", "swiftui", "ios development"])
    v.add("Android", ["android", "jetpack compose", "android sdk"])
    v.add("React Native", ["react native", "flutter"])

    # ------------------------------------------------------- infra / devops
    v.add("Docker", ["docker", "containers", "containerisation", "containerization"])
    v.add("Kubernetes", ["kubernetes", "k8s", "eks", "gke", "aks"])
    v.add("AWS", ["aws", "amazon web services", "ec2", "s3", "lambda"])
    v.add("Azure", ["azure", "microsoft azure"])
    v.add("GCP", ["gcp", "google cloud"])
    v.add("Terraform", ["terraform", "infrastructure as code", "iac", "pulumi"])
    v.add("CI/CD", ["ci/cd", "cicd", "continuous integration", "jenkins",
                    "github actions", "gitlab ci", "circleci"])
    v.add("Linux", ["linux", "unix", "bash", "shell scripting"])
    v.add("Monitoring", ["monitoring", "observability", "prometheus", "grafana",
                         "datadog", "new relic"])
    v.add("Git", ["git", "github", "gitlab", "bitbucket", "version control"])

    # ------------------------------------------------------ security / QA
    v.add("Security", ["security", "appsec", "penetration testing", "owasp",
                       "vulnerability"])
    v.add("Testing", ["unit testing", "integration testing", "test automation",
                      "pytest", "junit", "jest", "selenium", "cypress"])
    v.add("QA", ["quality assurance", "manual testing", "test cases", "qa"])

    # --------------------------------------------------------- ml / ai
    v.add("Machine Learning", ["machine learning", "ml", "deep learning",
                               "neural networks", "scikit-learn", "sklearn"])
    v.add("PyTorch", ["pytorch", "tensorflow", "keras"])
    v.add("NLP", ["nlp", "natural language processing", "llm", "large language model"])
    v.add("Computer Vision", ["computer vision", "opencv", "image recognition"])
    v.add("MLOps", ["mlops", "model deployment", "feature store"])

    # -------------------------------------------------------- product / pm
    v.add("Roadmapping", ["roadmap", "roadmapping", "product roadmap", "prioritisation",
                          "prioritization"])
    v.add("A/B Testing", ["a/b testing", "ab testing", "split testing",
                          "experimentation"])
    v.add("Stakeholder Management", ["stakeholder management", "stakeholder",
                                     "cross-functional"])
    v.add("Agile", ["agile", "scrum", "kanban", "sprint planning", "safe"])
    v.add("User Research", ["user research", "user interviews", "usability testing",
                            "discovery"])
    v.add("Product Analytics", ["amplitude", "mixpanel", "product analytics",
                                "heap", "pendo"])
    v.add("Jira", ["jira", "atlassian", "confluence", "linear", "asana"])

    # ---------------------------------------------------------- design
    v.add("Figma", ["figma", "sketch", "adobe xd"])
    v.add("UX Design", ["ux design", "user experience", "wireframes", "prototyping",
                        "interaction design"])
    v.add("UI Design", ["ui design", "visual design", "design systems", "typography"])
    v.add("Adobe Creative Suite", ["photoshop", "illustrator", "indesign",
                                   "after effects", "adobe creative"])

    # ------------------------------------------------------- marketing
    v.add("SEO", ["seo", "search engine optimisation", "search engine optimization"])
    v.add("SEM", ["sem", "google ads", "paid search", "ppc", "adwords"])
    v.add("Content Marketing", ["content marketing", "copywriting", "content strategy",
                                "editorial"])
    v.add("Email Marketing", ["email marketing", "mailchimp", "klaviyo",
                              "marketing automation", "hubspot"])
    v.add("Social Media", ["social media", "community management", "instagram",
                           "linkedin marketing"])
    v.add("Google Analytics", ["google analytics", "ga4", "web analytics"])
    v.add("Brand", ["brand strategy", "brand management", "positioning"])

    # ---------------------------------------------------------- sales / cs
    v.add("CRM", ["crm", "salesforce", "pipedrive", "zoho"])
    v.add("Lead Generation", ["lead generation", "prospecting", "outbound", "sdr"])
    v.add("Account Management", ["account management", "customer success",
                                 "client relationship", "renewals"])
    v.add("Negotiation", ["negotiation", "closing", "deal management"])

    # --------------------------------------------------------- finance / ops
    v.add("Financial Modelling", ["financial modelling", "financial modeling",
                                  "forecasting", "budgeting", "valuation"])
    v.add("Accounting", ["accounting", "bookkeeping", "gaap", "ifrs",
                         "accounts payable", "reconciliation"])
    v.add("Supply Chain", ["supply chain", "logistics", "procurement", "inventory"])
    v.add("Project Management", ["project management", "pmp", "prince2", "gantt"])
    v.add("Process Improvement", ["process improvement", "lean", "six sigma",
                                  "operational excellence"])

    # ------------------------------------------------------------- people
    v.add("Recruiting", ["recruiting", "recruitment", "talent acquisition", "sourcing",
                         "ats"])
    v.add("HR", ["human resources", "hris", "workday", "employee relations",
                 "onboarding"])
    v.add("Learning & Development", ["learning and development", "l&d", "training",
                                     "coaching"])

    # ---------------------------------------------------- healthcare / other
    v.add("Clinical Care", ["patient care", "clinical", "bedside", "triage"])
    v.add("Medical Records", ["emr", "ehr", "epic systems", "medical records"])
    v.add("Compliance", ["compliance", "regulatory", "gdpr", "hipaa", "sox", "audit"])
    v.add("Teaching", ["teaching", "curriculum", "lesson planning", "classroom"])

    # ---------------------------------------------------------- soft-ish
    v.add("Communication", ["written communication", "presentation skills",
                            "public speaking"])
    v.add("Leadership", ["team leadership", "people management", "mentoring",
                         "line management"])
    v.add("Problem Solving", ["problem solving", "analytical thinking",
                              "critical thinking"])
