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
    ("R", ["r programming", "rstudio", "r language", "r script"]),
]

BACKEND = [
    ("Spring", ["spring", "spring boot", "springboot"]),
    ("Node.js", ["node.js", "nodejs", "node", "express.js", "expressjs"]),
    ("Django", ["django"]),
    ("Flask", ["flask", "fastapi"]),
    ("API Design", ["api design", "rest api", "restful", "graphql",
                    "api development"]),
    ("Microservices", ["microservices", "microservice architecture"]),
    ("System Design", ["system design", "distributed systems", "architecture design",
                       "scalable systems"]),
]

FRONTEND = [
    ("React", ["react", "react.js", "reactjs",]),
    ("Angular", ["angular", "angularjs"]),
    ("Vue", ["vue", "vue.js", "vuejs", "nuxt", "nuxt.js"]),
    ("HTML/CSS", ["html", "css", "html5", "css3", "scss", "sass"]),
    ("Accessibility", ["accessibility", "wcag", "a11y", "screen reader"]),
]

DATA = [
    ("SQL", ["sql", "postgresql", "postgres", "mysql", "t-sql", "psql",
             "sql server", "sqlite"]),
    ("NoSQL", ["nosql", "mongodb", "mongo", "cassandra"]),
    ("Data Analysis", ["data analysis", "data analytics", "analytics",
                       "quantitative analysis"]),
    ("Excel", ["excel", "spreadsheets", "google sheets", "advanced excel"]),
    ("Tableau", ["tableau"]),
    ("Power BI", ["power bi", "powerbi"]),
    ("Looker", ["looker", "lookml"]),
    ("dbt", ["dbt", "data build tool"]),
    ("Snowflake", ["snowflake"]),
    ("Spark", ["spark", "apache spark"]),
    ("Airflow", ["airflow", "apache airflow"]),
    ("ETL", ["etl", "elt", "data pipeline", "data pipelines", "data engineering"]),
    ("Pandas", ["pandas", "numpy"]),
    ("Statistics", ["statistics", "statistical analysis", "statistical modelling",
                    "statistical modeling"]),
    ("Machine Learning", ["machine learning", "ml", "deep learning",
                          "neural networks", "pytorch", "tensorflow", "scikit-learn"]),
    ("Data Warehousing", ["data warehouse", "data warehousing"]),
]

INFRA = [
    ("AWS", ["aws", "amazon web services", "s3 bucket"]),
    ("Azure", ["azure", "microsoft azure"]),
    ("GCP", ["gcp", "google cloud", "google cloud platform"]),
    ("Docker", ["docker", "containerisation", "containerization", "containers"]),
    ("Kubernetes", ["kubernetes", "k8s"]),
    ("Terraform", ["terraform", "infrastructure as code", "iac"]),
    ("CI/CD", ["ci/cd", "cicd", "continuous integration", "continuous deployment"]),
    ("Linux", ["linux", "unix", "bash"]),
    ("Kafka", ["kafka", "apache kafka", "event streaming"]),
    ("Monitoring", ["monitoring", "new relic"]),
    ("Git", ["github", "gitlab", "version control", "bitbucket", "git workflow"]),
]

SECURITY = [
    ("Security", ["security", "cybersecurity", "infosec", "application security"]),
    ("Penetration Testing", ["penetration testing", "pen testing", "pentest",
                             "ethical hacking"]),
    ("Compliance", ["compliance", "pci dss"]),
    ("Networking", ["networking", "tcp/ip", "dns", "firewall", "ccna"]),
]

TESTING = [
    ("Automated Testing", ["automated testing", "test automation", "selenium", "unit testing", "pytest",
                           "junit", "jest"]),
    ("QA", ["qa", "quality assurance", "manual testing", "test cases"]),
]

PRODUCT = [
    ("Roadmapping", ["roadmap", "roadmapping", "product roadmap", "prioritisation",
                     "prioritization"]),
    ("A/B Testing", ["a/b testing", "ab testing", "split testing", "experimentation"]),
    ("User Research", ["user research", "customer research", "user interviews",
                       "usability testing", "discovery research", "discovery interviews"]),
    ("Stakeholder Management", ["stakeholder management", "stakeholder",
                                "cross-functional", "cross functional"]),
    ("Agile", ["agile", "scrum", "kanban", "sprint planning", "safe"]),
    ("Jira", ["jira", "atlassian", "confluence", "linear", "asana"]),
    ("Amplitude", ["amplitude"]),
    ("Mixpanel", ["mixpanel"]),
    ("Google Analytics", ["google analytics", "ga4"]),
    ("Product Strategy", ["product strategy", "product vision", "go-to-market",
                          "go to market"]),
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
    ("Negotiation", ["negotiation", "closing"]),
]

FINANCE = [
    ("Financial Modelling", ["financial modelling", "financial modeling",
                             "forecasting", "budgeting"]),
    ("Accounting", ["accounting", "bookkeeping"]),
    ("Audit", ["auditing", "internal audit", "audit experience"]),
    ("Tax", ["taxation", "gst filing", "vat returns", "tax compliance"]),
    ("ERP", ["erp", "tally"]),
]

OPERATIONS = [
    ("Project Management", ["project management", "pmp", "prince2",
                            "programme management", "program management"]),
    ("Process Improvement", ["process improvement",
                             "operational excellence"]),
    ("Supply Chain", ["supply chain", "inventory"]),
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
    ("HR", ["hr", "human resources", "workday", "people operations"]),
    ("Onboarding", ["onboarding", "employee onboarding"]),
    ("Performance Management", ["performance management", "performance reviews"]),
]

HEALTHCARE = [
    ("Patient Care", ["patient care", "clinical care", "bedside"]),
    ("Clinical Documentation", ["clinical documentation", "emr", "ehr", "charting"]),
    ("Medical Coding", ["medical coding", "cpt coding"]),
    ("Regulatory Affairs", ["regulatory affairs", "fda"]),
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


# --------------------------------------------------------------------------
# Second expansion. The first took the list from 15 to 108 (see HANDOVER 9.4);
# this one closes the gaps a coverage probe found against 125 common
# technologies, where 75 were unknown.
#
# THE TWO TRAPS FROM LAST TIME, STILL IN FORCE
#   1. No canonical or alias that is an ordinary English word. "Vault",
#      "Lake", "Segment", "Lever" and "Greenhouse" are all real products and
#      all real words — each is entered only in a form that cannot fire on
#      prose ("HashiCorp Vault", "Delta Lake", "Segment analytics").
#   2. No alias so generic it fires on every posting. "hiring" once matched
#      96 of 96. Anything that reads as a verb or a job-posting noun is out.
# --------------------------------------------------------------------------

BACKEND_EXT = [
    ("Redis", ["redis", "elasticache", "redis cache"]),
    ("Elasticsearch", ["elasticsearch", "elastic search", "opensearch"]),
    ("RabbitMQ", ["rabbitmq", "rabbit mq", "amqp"]),
    ("Celery", ["celery beat", "celery worker", "celery workers",
                "celery task", "celery tasks", "python celery"]),
    ("gRPC", ["grpc", "protobuf", "protocol buffers"]),
    ("WebSockets", ["websocket", "websockets", "socket.io", "socketio"]),
    ("OAuth", ["oauth", "oauth2", "oauth 2.0", "openid connect", "oidc", "saml",
               "keycloak", "auth0"]),
    ("Nginx", ["nginx", "traefik", "load balancer config"]),
    ("Payments Integration", ["stripe", "braintree", "paypal api", "razorpay",
                              "payment gateway"]),
]

FRONTEND_EXT = [
    ("Next.js", ["next.js", "nextjs", "next js"]),
    ("Svelte", ["svelte", "sveltekit", "svelte kit"]),
    ("Astro", ["astro framework", "astro.build"]),
    ("Build Tooling", ["webpack", "vite", "rollup", "esbuild", "turbopack"]),
    ("Storybook", ["storybook"]),
    ("Tailwind", ["tailwind", "tailwindcss", "tailwind css"]),
    ("Web Performance", ["core web vitals", "lighthouse audit", "web vitals",
                         "largest contentful paint"]),
]

DATA_EXT = [
    ("Databricks", ["databricks", "unity catalog"]),
    ("Flink", ["apache flink", "flink"]),
    ("Iceberg", ["apache iceberg", "iceberg table", "iceberg tables"]),
    ("Data Ingestion", ["fivetran", "airbyte", "stitch data"]),
    ("BI Tooling", ["metabase", "apache superset", "quicksight"]),
    ("Vector Search", ["pinecone", "weaviate", "milvus", "faiss", "qdrant",
                       "vector database", "vector db"]),
    ("Feature Store", ["feature store", "feast feature", "tecton"]),
    ("MLOps", ["mlflow", "weights & biases", "weights and biases", "wandb",
               "kubeflow", "sagemaker pipelines", "model registry"]),
    ("LLM Engineering", ["langchain", "llamaindex", "llama index", "rag pipeline",
                         "retrieval augmented", "prompt engineering",
                         "hugging face", "huggingface"]),
]

INFRA_EXT = [
    ("Ansible", ["ansible", "ansible playbook"]),
    ("Helm", ["helm chart", "helm charts", "helmfile", "helm release"]),
    ("GitOps", ["argocd", "argo cd", "fluxcd", "flux cd", "gitops"]),
    ("Observability", ["opentelemetry", "otel", "sentry",
                       "distributed tracing", "jaeger"]),
    ("Secrets Management", ["hashicorp vault", "aws secrets manager",
                            "secret manager", "sops"]),
    ("Service Mesh", ["istio", "linkerd", "service mesh"]),
    ("Scripting", ["powershell", "shell scripting", "bash scripting"]),
]

TESTING_EXT = [
    ("Playwright", ["playwright"]),
    ("Cypress", ["cypress"]),
    ("Contract Testing", ["testcontainers", "pact testing", "contract tests"]),
    ("Load Testing", ["k6 load", "jmeter", "locust load", "gatling"]),
]

MOBILE = [
    ("React Native", ["react native", "react-native"]),
    ("Flutter", ["flutter", "dart flutter"]),
    ("iOS Development", ["ios development", "objective-c", "xcode", "app store connect"]),
    ("Android Development", ["android development", "jetpack compose", "android sdk"]),
]

TOOLS = [
    ("No-code Automation", ["zapier", "airtable", "make.com", "n8n"]),
    ("Webflow", ["webflow"]),
    ("Framer", ["framer motion", "framer.com", "framer sites"]),
    ("Notion", ["notion workspace", "notion database"]),
]

MARKETING_EXT = [
    ("SEO Tooling", ["ahrefs", "semrush", "screaming frog", "moz pro"]),
    ("Product Analytics", ["segment analytics", "heap analytics", "posthog"]),
]

SALES_EXT = [
    ("Sales Engagement", ["salesloft", "outreach.io", "apollo.io", "gong.io"]),
    ("Sales Methodology", ["meddic", "meddpicc", "challenger sale", "spin selling",
                           "bant qualification"]),
]

PEOPLE_EXT = [
    ("HRIS", ["workday hris", "bamboohr", "hibob", "rippling"]),
    ("ATS Tooling", ["greenhouse ats", "lever ats", "ashby ats", "workable"]),
    ("Engagement Tooling", ["culture amp", "lattice performance", "15five"]),
]


# --------------------------------------------------------------------------
# CYBERSECURITY — first domain of the Phase 2 expansion.
#
# Validated against a real Dubai SOC-lead posting and the resume that was sent
# to it. Before this block the vocabulary recognised six things in that
# posting — Azure, Compliance, Monitoring, Networking, Security, and Product
# Strategy, the last of which fired on "GTM" meaning F5 Global Traffic
# Manager. Forty-two real security terms were invisible, so the tool told an
# eight-year SOC lead he was missing "Account Management" and "Leadership".
#
# Rules that shaped the entries below:
#   * "SOC" is never a bare alias — it collides with SOC 2 and with ordinary
#     prose. Only "soc operations", "soc analyst" and similar.
#   * "Aruba" and "Sentinel" are a country and a common noun; both are entered
#     only in multi-word forms.
#   * Certifications are their own skills. A posting asking for CEH and a
#     resume holding CEH should match on it.
# --------------------------------------------------------------------------

CYBERSECURITY = [
    # --- detection and response ---
    ("SIEM", ["siem", "security information and event management",
              "log correlation", "correlation rules"]),
    ("QRadar", ["qradar", "ibm qradar"]),
    ("Microsoft Sentinel", ["microsoft sentinel", "azure sentinel"]),
    ("Splunk", ["splunk", "splunk es", "splunk enterprise security"]),
    ("LogRhythm", ["logrhythm"]),
    ("SOAR", ["soar", "security orchestration", "automated playbook"]),
    ("Security Operations Centre", ["security operations center",
                                    "security operations centre",
                                    "soc operations", "soc analyst",
                                    "soc playbook", "24/7 soc", "level 2/3"]),
    ("Incident Response", ["incident response", "incident handling",
                           "containment and eradication", "post-incident review",
                           "root-cause analysis"]),
    ("Threat Hunting", ["threat hunting", "threat hunt", "proactive hunting"]),
    ("Threat Intelligence", ["threat intelligence", "threat intel",
                             "indicators of compromise", "ioc feed"]),
    ("Digital Forensics", ["digital forensics", "forensic investigation",
                           "dfir", "memory forensics"]),

    # --- endpoint ---
    ("EDR/XDR", ["edr", "xdr", "edr/xdr", "endpoint detection and response"]),
    ("Microsoft Defender", ["microsoft defender", "defender for endpoint",
                            "defender atp", "defender for cloud"]),
    ("CrowdStrike", ["crowdstrike", "falcon sensor"]),
    ("SentinelOne", ["sentinelone"]),
    ("Carbon Black", ["carbon black", "cb defense", "cb response"]),
    ("Microsoft Intune", ["microsoft intune", "intune", "endpoint manager"]),

    # --- vulnerability ---
    ("Vulnerability Management", ["vulnerability management",
                                  "vulnerability assessment",
                                  "vulnerability scanning", "patch management",
                                  "remediation tracking"]),
    ("Qualys", ["qualys"]),
    ("Tenable", ["tenable", "nessus", "tenable.io"]),
    ("Rapid7", ["rapid7", "insightvm", "nexpose"]),
    ("Burp Suite", ["burp suite", "burpsuite"]),
    ("Metasploit", ["metasploit"]),

    # --- network security ---
    ("Firewall Administration", ["next-generation firewall", "ngfw",
                                 "firewall administration", "firewall policy",
                                 "firewall management"]),
    ("Palo Alto", ["palo alto", "pan-os", "panorama firewall"]),
    ("FortiGate", ["fortigate", "fortinet", "fortimanager", "fortianalyzer"]),
    ("Check Point", ["check point", "checkpoint firewall", "checkpoint gaia"]),
    ("Cisco Firepower", ["cisco ftd", "firepower", "sourcefire", "cisco asa"]),
    ("Juniper SRX", ["juniper srx", "srx firewall"]),
    ("F5 BIG-IP", ["f5 big-ip", "big-ip ltm", "big-ip gtm", "big-ip asm",
                   "big-ip apm", "big-ip afm", "f5 load balancer"]),
    ("IDS/IPS", ["ids/ips", "intrusion detection", "intrusion prevention",
                 "ngips", "snort", "suricata"]),
    ("Secure Web Gateway", ["secure web gateway", "blue coat", "bluecoat",
                            "proxy sg", "zscaler"]),
    ("Forcepoint", ["forcepoint"]),
    ("Network Access Control", ["network access control", "forescout",
                                "cisco ise", "identity services engine",
                                "802.1x"]),
    ("VPN", ["vpn/ipsec", "ipsec tunnel", "ssl vpn", "remote access vpn",
             "site-to-site vpn"]),

    # --- identity and privilege ---
    ("Privileged Access Management", ["privileged access management",
                                      "pam solution", "credential vaulting",
                                      "least privilege", "privileged session"]),
    ("CyberArk", ["cyberark"]),
    ("ARCON", ["arcon pam", "arcon"]),
    ("BeyondTrust", ["beyondtrust", "thycotic", "delinea"]),
    ("Identity and Access Management", ["identity and access management",
                                        "single sign-on", "sso",
                                        "multi-factor authentication", "mfa"]),
    ("Active Directory", ["active directory", "azure active directory",
                          "entra id", "group policy", "ldap"]),

    # --- data protection ---
    ("Data Loss Prevention", ["data loss prevention", "dlp",
                              "data exfiltration"]),
    ("Imperva", ["imperva", "imperva dam", "database activity monitoring"]),
    ("Encryption", ["encryption at rest", "tls/ssl", "pki",
                    "certificate management", "key management"]),

    # --- governance ---
    ("MITRE ATT&CK", ["mitre att&ck", "mitre attack", "att&ck framework"]),
    ("NIST", ["nist", "nist csf", "nist cybersecurity framework", "nist 800-53"]),
    ("ISO 27001", ["iso 27001", "iso27001", "isms"]),
    ("SOC 2", ["soc 2", "soc2", "soc ii"]),
    ("GRC", ["governance risk and compliance", "grc", "risk register",
             "control framework"]),
    ("Zero Trust", ["zero trust", "microsegmentation", "network segmentation"]),

    # --- certifications ---
    ("CEH", ["ceh", "certified ethical hacker"]),
    ("CISSP", ["cissp"]),
    ("CISM", ["cism"]),
    ("OSCP", ["oscp"]),
    ("Security+", ["comptia security+", "security+"]),
    ("CCIE", ["ccie"]),
    ("MCSE", ["mcse", "mcp certification"]),
    ("AZ-500", ["az-500", "azure security engineer"]),

    # --- infrastructure seen alongside security work ---
    ("SolarWinds", ["solarwinds"]),
    ("PRTG", ["prtg"]),
    ("Infoblox", ["infoblox", "ipam", "ddi"]),
    ("Aruba", ["aruba wireless", "aruba clearpass", "aruba networks"]),
    ("Brocade", ["brocade switch", "brocade fabric"]),
]


# --------------------------------------------------------------------------
# DATA ENGINEERING — second domain of the Phase 2 expansion.
#
# The existing "data" domain covered the vocabulary of an analyst: SQL, Excel,
# Tableau, Pandas. A data ENGINEER's posting is a different list — warehouses,
# orchestration, streaming, formats, governance and the cloud-native services
# that have replaced half of it.
#
# Guarded names in here, and why:
#   * "Beam", "Arrow", "Hive", "Pig", "Storm", "Presto", "Drill", "Atlas" and
#     "Nomad" are all ordinary words. Each is entered in a qualified form only.
#   * "Glue", "Fabric", "Fusion" and "Composer" likewise — AWS Glue and
#     Microsoft Fabric are products; glue and fabric are things.
#   * "dbt" already exists; "Snowflake", "Airflow" and "Spark" too. Nothing
#     here re-declares them.
# --------------------------------------------------------------------------

DATA_ENGINEERING = [
    # --- warehouses and lakehouses ---
    ("BigQuery", ["bigquery", "google bigquery", "bq"]),
    ("Redshift", ["redshift", "amazon redshift"]),
    ("Synapse", ["azure synapse", "synapse analytics"]),
    ("ClickHouse", ["clickhouse"]),
    ("DuckDB", ["duckdb"]),
    ("Teradata", ["teradata"]),
    ("Lakehouse", ["lakehouse", "delta lake", "medallion architecture"]),

    # --- orchestration ---
    ("Dagster", ["dagster"]),
    ("Prefect", ["prefect flow", "prefect orchestration"]),
    ("Cloud Composer", ["cloud composer", "managed airflow"]),
    ("Step Functions", ["step functions", "aws step functions"]),

    # --- streaming ---
    ("Kinesis", ["kinesis", "aws kinesis", "kinesis firehose"]),
    ("Pub/Sub", ["pub/sub", "google pub/sub", "cloud pubsub"]),
    ("Event Hubs", ["event hubs", "azure event hubs"]),
    ("Debezium", ["debezium", "change data capture", "cdc pipeline"]),
    ("Kafka Connect", ["kafka connect", "ksqldb", "schema registry"]),

    # --- processing ---
    ("Hadoop", ["hadoop", "hdfs", "mapreduce", "apache hive", "hive metastore"]),
    ("Presto", ["presto sql", "prestodb", "trino"]),
    ("Beam", ["apache beam", "dataflow pipeline"]),
    ("Ray", ["ray cluster", "ray serve", "ray tune"]),
    ("Polars", ["polars"]),
    ("PySpark", ["pyspark"]),

    # --- ingestion and integration ---
    ("AWS Glue", ["aws glue", "glue catalog", "glue job"]),
    ("Azure Data Factory", ["azure data factory", "adf pipeline"]),
    ("Informatica", ["informatica", "powercenter"]),
    ("Talend", ["talend"]),
    ("Meltano", ["meltano", "singer tap"]),

    # --- formats and storage ---
    ("Parquet", ["parquet", "columnar format"]),
    ("Avro", ["avro schema", "apache avro"]),
    ("Data Modelling", ["data modelling", "data modeling", "star schema",
                        "dimensional modelling", "slowly changing dimension",
                        "kimball", "data vault"]),

    # --- governance and quality ---
    ("Data Governance", ["data governance", "data catalog", "data lineage",
                         "apache atlas", "collibra", "alation"]),
    ("Data Quality", ["data quality", "great expectations", "data contract",
                      "soda core", "anomaly detection in pipelines"]),
    ("Master Data Management", ["master data management", "mdm"]),
    ("GDPR", ["gdpr", "data subject request", "right to erasure"]),

    # --- ops ---
    ("Data Observability", ["data observability", "monte carlo data",
                            "pipeline monitoring", "freshness sla"]),
    ("Cost Optimisation", ["cost optimisation", "cost optimization",
                           "finops", "warehouse cost"]),
]


# --------------------------------------------------------------------------
# CLOUD & DEVOPS — third domain of the Phase 2 expansion.
#
# The existing "infra" domain names the three clouds and four tools. A real
# platform or SRE posting names services: EKS not "Kubernetes", CloudFormation
# not "infrastructure as code", and half a dozen things whose only appearance
# in a resume is an acronym.
#
# Guarded here, and why: "Fargate", "Lambda", "Route", "Consul", "Nomad",
# "Packer", "Vagrant", "Chef", "Puppet", "Salt", "Sentry", "Pager" — some are
# ordinary words, some are Greek letters, and Chef/Puppet/Salt are the classic
# case this whole rule exists for. Each is entered only in a qualified form.
# --------------------------------------------------------------------------

CLOUD_DEVOPS = [
    # --- AWS services ---
    ("EC2", ["amazon ec2", "ec2 instance"]),
    ("Lambda", ["aws lambda", "lambda function", "serverless function"]),
    ("EKS", ["amazon eks", "eks cluster"]),
    ("ECS", ["amazon ecs", "ecs fargate", "fargate task"]),
    ("CloudFormation", ["cloudformation", "cfn template", "aws cdk"]),
    ("CloudWatch", ["cloudwatch", "cloudwatch alarm"]),
    ("Route 53", ["route 53", "route53"]),
    ("IAM Policy", ["iam policy", "iam role", "assume role", "least-privilege iam"]),
    ("RDS", ["amazon rds", "aurora postgres", "aurora mysql"]),
    ("DynamoDB", ["dynamodb"]),
    ("SQS", ["amazon sqs", "sqs queue"]),
    ("SNS", ["amazon sns"]),

    # --- Azure and GCP ---
    ("AKS", ["azure kubernetes service", "aks cluster"]),
    ("Azure DevOps", ["azure devops", "azure pipelines", "vsts"]),
    ("Azure Functions", ["azure functions"]),
    ("GKE", ["google kubernetes engine", "gke cluster"]),
    ("Cloud Run", ["cloud run", "google cloud run"]),
    ("Cloud Build", ["cloud build", "google cloud build"]),

    # --- infrastructure as code ---
    ("Pulumi", ["pulumi"]),
    ("Packer", ["hashicorp packer", "packer image"]),
    ("Vagrant", ["hashicorp vagrant"]),
    ("Chef", ["chef cookbook", "chef infra", "chef automate"]),
    ("Puppet", ["puppet manifest", "puppet enterprise"]),
    ("SaltStack", ["saltstack", "salt minion"]),
    ("Crossplane", ["crossplane"]),

    # --- CI/CD ---
    ("GitHub Actions", ["github actions", "gh actions", "actions workflow"]),
    ("GitLab CI", ["gitlab ci", "gitlab runner", ".gitlab-ci.yml"]),
    ("Jenkins", ["jenkins", "jenkinsfile", "jenkins pipeline"]),
    ("CircleCI", ["circleci"]),
    ("Argo Workflows", ["argo workflows", "argo events"]),
    ("Artifact Registry", ["artifact registry", "container registry",
                           "jfrog artifactory", "nexus repository"]),

    # --- runtime and orchestration ---
    ("Nomad", ["hashicorp nomad"]),
    ("Consul", ["hashicorp consul", "consul connect"]),
    ("OpenShift", ["openshift", "okd"]),
    ("Rancher", ["rancher kubernetes", "rancher labs", "rancher rke",
                 "rke2"]),
    ("Kustomize", ["kustomize"]),
    ("Operators", ["kubernetes operator", "custom resource definition", "crd"]),

    # --- reliability ---
    ("Site Reliability Engineering", ["site reliability", "sre practice",
                                      "error budget", "toil reduction"]),
    ("SLO", ["slo", "sli", "service level objective", "service level indicator"]),
    ("Incident Management", ["pagerduty", "opsgenie", "on-call rotation",
                             "runbook", "blameless postmortem"]),
    ("Chaos Engineering", ["chaos engineering", "chaos monkey", "fault injection"]),
    ("Load Balancing", ["load balancing", "nginx ingress", "envoy proxy",
                        "haproxy", "reverse proxy"]),
    ("Autoscaling", ["autoscaling", "horizontal pod autoscaler", "hpa",
                     "cluster autoscaler"]),
    ("Disaster Recovery", ["disaster recovery", "rpo", "rto",
                           "backup and restore", "failover testing"]),

    # --- metrics and logging ---
    ("Prometheus", ["prometheus", "promql", "alertmanager"]),
    ("Grafana", ["grafana", "grafana dashboard", "loki logs"]),
    ("ELK Stack", ["elk stack", "logstash", "kibana", "filebeat"]),
    ("Datadog", ["datadog"]),
    ("Splunk Observability", ["splunk observability", "signalfx"]),

    # --- platform ---
    ("Platform Engineering", ["platform engineering", "internal developer platform",
                              "backstage", "golden path"]),
    ("Cost Management", ["cloud cost management", "reserved instances",
                         "spot instances", "rightsizing"]),
    ("Networking (Cloud)", ["vpc", "subnet", "security group", "nat gateway",
                            "transit gateway", "peering connection"]),
]


# --------------------------------------------------------------------------
# HEALTHCARE — fourth domain of the Phase 2 expansion.
#
# Four entries covered this before: Patient Care, Clinical Documentation,
# Medical Coding, Regulatory Affairs. A nursing or clinical-ops posting names
# systems, certifications and settings, and none of them existed — which is
# why the registered-nurse job description in the harness scored 33%.
#
# Guarded: "Epic", "Cerner", "Meditech" are vendors; "epic" is also an English
# adjective and appears in marketing copy constantly.
# --------------------------------------------------------------------------

HEALTHCARE_EXT = [
    # --- systems ---
    ("Epic", ["epic systems", "epic emr", "epic ehr", "epic hyperspace"]),
    ("Cerner", ["cerner", "cerner millennium", "oracle health"]),
    ("Meditech", ["meditech"]),
    ("Allscripts", ["allscripts", "veradigm"]),
    ("HL7", ["hl7", "fhir", "hl7 interface"]),
    ("PACS", ["pacs", "picture archiving", "dicom"]),

    # --- clinical practice ---
    ("Triage", ["patient triage", "triage protocol", "acuity assessment",
                "triage nurse"]),
    ("Medication Administration", ["medication administration",
                                   "medication reconciliation", "pharmacology",
                                   "iv therapy"]),
    ("Care Planning", ["care plan", "care planning", "discharge planning",
                       "case management"]),
    ("Infection Control", ["infection control", "sterile technique",
                           "aseptic technique", "ppe protocol"]),
    ("Vital Signs", ["vital signs", "patient monitoring", "telemetry"]),
    ("Wound Care", ["wound care", "wound dressing", "pressure ulcer"]),
    ("Phlebotomy", ["phlebotomy", "venipuncture", "blood draw"]),

    # --- certifications ---
    ("BLS", ["basic life support", "bls certification"]),
    ("ACLS", ["advanced cardiac life support", "acls certification"]),
    ("PALS", ["pediatric advanced life support", "pals certification"]),
    ("RN Licence", ["registered nurse", "rn licence", "rn license", "nclex"]),

    # --- settings and governance ---
    ("Acute Care", ["acute care", "intensive care", "icu", "emergency department",
                    "med-surg", "medical-surgical"]),
    ("HIPAA", ["hipaa", "protected health information", "phi"]),
    ("Joint Commission", ["joint commission", "jcaho", "cqc inspection"]),
    ("Clinical Trials", ["clinical trial", "gcp compliance", "irb submission",
                         "protocol deviation"]),
    ("Revenue Cycle", ["revenue cycle", "prior authorisation",
                       "prior authorization", "claims denial", "icd-10", "cpt code"]),
]


# --------------------------------------------------------------------------
# FINANCE & ACCOUNTING — fifth domain.
#
# Five entries covered this. A controller, analyst or audit posting names
# standards, systems and processes; the financial-analyst job description in
# the harness matched almost nothing.
#
# Guarded: "Sage", "Prime", "Netting", "Close" and "Basel" — all products or
# terms of art that are also ordinary words.
# --------------------------------------------------------------------------

FINANCE_EXT = [
    # --- standards ---
    ("GAAP", ["gaap", "us gaap", "generally accepted accounting"]),
    ("IFRS", ["ifrs", "ias standard"]),
    ("SOX", ["sarbanes-oxley", "sox compliance", "sox 404", "internal controls"]),
    ("Basel", ["basel iii", "basel ii", "capital adequacy"]),
    ("Transfer Pricing", ["transfer pricing", "arm's length principle"]),

    # --- systems ---
    ("SAP", ["sap fico", "sap s/4hana", "sap erp"]),
    ("Oracle Financials", ["oracle financials", "oracle fusion", "peoplesoft"]),
    ("NetSuite", ["netsuite"]),
    ("QuickBooks", ["quickbooks"]),
    ("Xero", ["xero accounting"]),
    ("Workday Financials", ["workday financials", "workday adaptive"]),
    ("Hyperion", ["hyperion", "oracle epm", "essbase"]),
    ("Anaplan", ["anaplan"]),
    ("Blackline", ["blackline"]),

    # --- process ---
    ("Month-End Close", ["month-end close", "period close", "close checklist",
                         "journal entry", "accruals"]),
    ("Reconciliation", ["account reconciliation", "bank reconciliation",
                        "balance sheet reconciliation", "intercompany"]),
    ("FP&A", ["fp&a", "financial planning and analysis", "budget vs actual",
              "variance analysis", "rolling forecast"]),
    ("Accounts Payable", ["accounts payable", "ap process", "three-way match",
                          "vendor payments"]),
    ("Accounts Receivable", ["accounts receivable", "ar ageing", "ar aging",
                             "collections process", "dso"]),
    ("Payroll", ["payroll processing", "payroll reconciliation"]),
    ("Treasury", ["treasury management", "cash flow forecasting",
                  "liquidity management", "fx hedging"]),
    ("Valuation", ["dcf model", "discounted cash flow", "comparable companies",
                   "precedent transactions", "lbo model"]),
    ("Cost Accounting", ["cost accounting", "standard costing", "cogs analysis",
                         "margin analysis"]),
    ("Financial Reporting", ["financial reporting", "statutory accounts",
                             "consolidation", "management accounts"]),

    # --- credentials ---
    ("CPA", ["cpa qualified", "certified public accountant"]),
    ("ACCA", ["acca"]),
    ("CFA", ["cfa charterholder", "cfa level"]),
    ("CIMA", ["cima qualified"]),
]


# --------------------------------------------------------------------------
# LEGAL & COMPLIANCE — sixth domain.
#
# Nothing covered this at all. A paralegal, contracts manager or compliance
# officer posting named zero recognisable skills before this block.
#
# English words needing a qualified form: "brief", "counsel", "discovery",
# "practice", "matter", "trust", "will", "bar". Legal vocabulary is unusually
# full of them, which is why every entry here is multi-word or an acronym.
# --------------------------------------------------------------------------

LEGAL = [
    ("Contract Drafting", ["contract drafting", "contract negotiation",
                           "contract review", "redlining", "term sheet"]),
    ("Commercial Agreements", ["master services agreement", "msa negotiation",
                               "statement of work", "nda review",
                               "licensing agreement", "sla negotiation"]),
    ("Corporate Governance", ["corporate governance", "board resolution",
                              "company secretarial", "articles of association"]),
    ("Litigation Support", ["litigation support", "e-discovery", "ediscovery",
                            "document review", "deposition preparation",
                            "case management system"]),
    ("Legal Research", ["legal research", "westlaw", "lexisnexis",
                        "case law research", "citation checking"]),
    ("Intellectual Property", ["intellectual property", "patent filing",
                               "trademark registration", "ip portfolio",
                               "copyright clearance"]),
    ("Data Privacy Law", ["data privacy law", "ccpa", "dpa 2018",
                          "data processing agreement", "privacy impact assessment",
                          "dpia"]),
    ("Regulatory Compliance", ["regulatory compliance", "aml compliance",
                               "kyc process", "know your customer",
                               "sanctions screening", "fatca"]),
    ("Employment Law", ["employment law", "tupe", "settlement agreement",
                        "disciplinary process", "employment tribunal"]),
    ("Contract Lifecycle Management", ["contract lifecycle management",
                                       "clm platform", "ironclad", "docusign clm",
                                       "contract repository"]),
    ("Legal Operations", ["legal operations", "legal ops", "outside counsel management",
                          "matter management", "legal spend"]),
    ("Due Diligence", ["due diligence", "m&a due diligence", "data room",
                       "vendor due diligence"]),
]


# --------------------------------------------------------------------------
# MANUFACTURING & SUPPLY CHAIN — seventh domain.
#
# "Supply Chain" was one entry. The vocabulary of the actual work — planning
# systems, lean methods, quality standards, logistics — was absent.
#
# English words needing a qualified form: "lean", "kanban" (already present),
# "yield", "batch", "plant", "cell", "line", "shift".
# --------------------------------------------------------------------------

MANUFACTURING = [
    # --- methods ---
    ("Lean Manufacturing", ["lean manufacturing", "lean six sigma", "5s workplace",
                            "value stream mapping", "kaizen", "poka-yoke"]),
    ("Six Sigma", ["six sigma", "dmaic", "green belt", "black belt"]),
    ("Total Productive Maintenance", ["total productive maintenance", "tpm",
                                      "preventive maintenance", "oee"]),
    ("Root Cause Analysis", ["root cause analysis", "5 whys", "fishbone diagram",
                             "ishikawa", "8d report"]),

    # --- quality ---
    ("Quality Management", ["quality management system", "qms", "iso 9001",
                            "capa process", "non-conformance"]),
    ("Statistical Process Control", ["statistical process control", "spc chart",
                                     "control chart", "process capability", "cpk"]),
    ("GMP", ["good manufacturing practice", "gmp compliance", "cgmp"]),
    ("Inspection", ["incoming inspection", "first article inspection",
                    "dimensional inspection", "gauge r&r"]),

    # --- planning and systems ---
    ("Production Planning", ["production planning", "master production schedule",
                             "capacity planning", "shop floor scheduling"]),
    ("MRP", ["material requirements planning", "mrp system", "mrp ii"]),
    ("Inventory Management", ["inventory management", "cycle counting",
                              "safety stock", "stock rotation", "abc analysis"]),
    ("Demand Planning", ["demand planning", "demand forecasting",
                         "sales and operations planning", "s&op"]),
    ("Warehouse Management", ["warehouse management system", "wms",
                              "pick and pack", "putaway", "cross-docking"]),
    ("Logistics", ["freight forwarding", "3pl management", "customs clearance",
                   "incoterms", "last-mile delivery"]),
    ("Procurement", ["strategic sourcing", "supplier negotiation",
                     "purchase order process", "rfq process", "spend analysis"]),

    # --- engineering ---
    ("CAD", ["autocad", "solidworks", "catia", "creo parametric"]),
    ("PLC", ["plc programming", "ladder logic", "scada system", "hmi design"]),
    ("Bill of Materials", ["bill of materials", "bom management", "engineering change order"]),
    ("Health and Safety", ["health and safety", "hse compliance", "osha",
                           "risk assessment method statement", "toolbox talk"]),
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
    "backend_ext": BACKEND_EXT,
    "frontend_ext": FRONTEND_EXT,
    "data_ext": DATA_EXT,
    "infra_ext": INFRA_EXT,
    "testing_ext": TESTING_EXT,
    "mobile": MOBILE,
    "tools": TOOLS,
    "marketing_ext": MARKETING_EXT,
    "sales_ext": SALES_EXT,
    "people_ext": PEOPLE_EXT,
    "cybersecurity": CYBERSECURITY,
    "data_engineering": DATA_ENGINEERING,
    "cloud_devops": CLOUD_DEVOPS,
    "healthcare_ext": HEALTHCARE_EXT,
    "finance_ext": FINANCE_EXT,
    "legal": LEGAL,
    "manufacturing": MANUFACTURING,
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


# --------------------------------------------------------------------------
# Presentation grouping
#
# 33 internal domains is the right granularity for maintaining this file and
# the wrong one for showing a reader. Forty chips in one undifferentiated wall
# is a list nobody reads; the same forty under six headings is a picture of
# what someone actually does.
#
# Certifications are pulled out separately because they answer a different
# question from tools — "am I qualified" rather than "have I used it".
# --------------------------------------------------------------------------

CERTIFICATIONS = {
    "CEH", "CISSP", "CISM", "OSCP", "Security+", "CCIE", "MCSE", "AZ-500",
    "ISO 27001", "SOC 2", "BLS", "ACLS", "PALS", "RN Licence", "CPA", "ACCA",
    "CFA", "CIMA", "GMP", "Six Sigma",
}

DISPLAY_GROUPS = {
    "cybersecurity": "Security",
    "cloud_devops": "Cloud & DevOps",
    "infra": "Cloud & DevOps",
    "infra_ext": "Cloud & DevOps",
    "data": "Data",
    "data_ext": "Data",
    "data_engineering": "Data",
    "programming": "Programming",
    "backend": "Backend",
    "backend_ext": "Backend",
    "frontend": "Frontend",
    "frontend_ext": "Frontend",
    "mobile": "Mobile",
    "testing": "Testing",
    "testing_ext": "Testing",
    "security": "Security",
    "product": "Product",
    "design": "Design",
    "marketing": "Marketing",
    "marketing_ext": "Marketing",
    "sales": "Sales",
    "sales_ext": "Sales",
    "finance": "Finance",
    "finance_ext": "Finance",
    "legal": "Legal & Compliance",
    "healthcare": "Healthcare",
    "healthcare_ext": "Healthcare",
    "manufacturing": "Manufacturing & Supply Chain",
    "operations": "Operations",
    "people": "People",
    "people_ext": "People",
    "tools": "Tools",
    "soft": "Ways of working",
}


def display_group(canonical: str) -> str:
    """Which heading a skill appears under on screen."""
    if canonical in CERTIFICATIONS:
        return "Certifications"
    for domain, entries in DOMAINS.items():
        for name, _ in entries:
            if name == canonical:
                return DISPLAY_GROUPS.get(domain, "Other")
    return "Other"
