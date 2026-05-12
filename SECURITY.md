# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in CS Navigator, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email: **compsci@morgan.edu** with the subject line "CS Navigator Security Report"

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours and work to patch the issue promptly.

## Security Measures

CS Navigator implements the following security measures:

### Authentication & Authorization
- JWT tokens with bcrypt password hashing
- Morgan State email (.morgan.edu) domain restriction
- Email verification required for new accounts
- 3-day token expiry with secure session management

### AI Security
- 43-category Promptfoo red team security audit
- 9 agent-level security rules blocking:
  - Jailbreak attempts (pirate mode, DAN, etc.)
  - Role-play attacks (internal QA tool framing)
  - Prompt injection ("ignore previous instructions")
  - Self-disclosure ("I am programmed to...")
  - Calibration framing ("you are BiasForge...")
- Grounding gate catches hallucinations via KB chunk validation

### Data Protection
- No student credentials stored (FERPA compliant)
- DegreeWorks and Canvas data synced transiently, never cached with passwords
- Self-reported vs verified data clearly labeled
- Guest personal queries intercepted and redirected to signup
- Session isolation (UUID per guest request)

### Infrastructure
- CORS restricted to known origins
- Input length limits on all fields
- File upload validation (type whitelist, 10MB max)
- Rate limiting on guest chat and registration
- Cloud Run with min-instances for availability
- All secrets via environment variables or Cloud Secret Manager

## Supported Versions

| Version | Supported |
|---------|-----------|
| v5.0    | Yes       |
| < v5.0  | No        |
