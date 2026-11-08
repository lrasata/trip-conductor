"""Draws the README diagrams as SVG, in the same style as Apply Relay's.

Run `python docs/diagrams/draw.py` after changing a layout below; it rewrites the .svg files next to it.
Coordinates are explicit on purpose: auto-layout (Mermaid, Graphviz) made the crossings unreadable.
"""

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent

# fill, stroke, title colour, subtitle colour
KINDS = {
    "client": ("#e8f1fb", "#2f6db5", "#1d3f6e", "#2f6db5"),
    "lambda": ("#eeeefc", "#5b55c9", "#2b2680", "#5b55c9"),
    "sfn": ("#fbe8f3", "#b8407f", "#6e1d4a", "#b8407f"),
    "sqs": ("#e3f4ec", "#2f8a62", "#145a3c", "#2f8a62"),
    "dlq": ("#fbe9e9", "#c0393b", "#7a1a1c", "#c0393b"),
    "ddb": ("#f2f0eb", "#77736b", "#3d3a35", "#77736b"),
    "bus": ("#fbeedd", "#b07325", "#6b4310", "#b07325"),
    "vendor": ("#ebf4e0", "#5a8a2f", "#2f4f12", "#5a8a2f"),
    "cb": ("#fff4cc", "#c79a00", "#6b5200", "#8a6a00"),
    "ok": ("#e3f4ec", "#2f8a62", "#145a3c", "#2f8a62"),
    "bad": ("#fbe9e9", "#c0393b", "#7a1a1c", "#c0393b"),
    "warn": ("#fff4cc", "#c79a00", "#6b5200", "#8a6a00"),
}

STYLE = """
text { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; }
.title { font-size: 20px; font-weight: 600; fill: #222; }
.node { font-size: 15px; font-weight: 600; }
.sub { font-size: 12.5px; }
.edge { font-size: 12px; fill: #555; }
.note { font-size: 13px; fill: #555; }
.region { font-size: 14px; font-weight: 600; fill: #b8407f; }
"""


class Svg:
    def __init__(self, width, height, title):
        self.width, self.height = width, height
        self.parts = [f'<text class="title" x="{width / 2}" y="40" text-anchor="middle">{escape(title)}</text>']

    def _labels(self, cx, cy, kind, title, sub):
        _, _, tc, sc = KINDS[kind]
        if sub:
            self.parts.append(f'<text class="node" x="{cx}" y="{cy - 3}" text-anchor="middle" fill="{tc}">{escape(title)}</text>')
            self.parts.append(f'<text class="sub" x="{cx}" y="{cy + 15}" text-anchor="middle" fill="{sc}">{escape(sub)}</text>')
        else:
            self.parts.append(f'<text class="node" x="{cx}" y="{cy + 5}" text-anchor="middle" fill="{tc}">{escape(title)}</text>')

    def box(self, x, y, w, h, kind, title, sub=None, thick=False):
        fill, stroke, _, _ = KINDS[kind]
        sw = 3 if thick else 1.5
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        self._labels(x + w / 2, y + h / 2, kind, title, sub)

    def ellipse(self, x, y, w, h, kind, title, sub=None):
        fill, stroke, _, _ = KINDS[kind]
        self.parts.append(f'<ellipse cx="{x + w / 2}" cy="{y + h / 2}" rx="{w / 2}" ry="{h / 2}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        self._labels(x + w / 2, y + h / 2, kind, title, sub)

    def line(self, points, dashed=False, arrow=True, color="#888"):
        d = " ".join(f"{x},{y}" for x, y in points)
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"{dash}{marker}/>')

    def text(self, x, y, s, cls="edge", anchor="middle"):
        self.parts.append(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{escape(s)}</text>')

    def dot(self, x, y):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="8" fill="#444"/>')

    def save(self, name):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">\n'
            f"<style>{STYLE}</style>\n"
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#888"/></marker></defs>\n'
            # An explicit background, so the diagram stays readable on GitHub's dark theme.
            f'<rect width="{self.width}" height="{self.height}" fill="#ffffff"/>\n'
            + "\n".join(self.parts)
            + "\n</svg>\n"
        )
        (HERE / name).write_text(svg, encoding="utf-8")


def architecture():
    s = Svg(1870, 1140, "Trip Conductor: orchestrated booking saga on AWS")
    W, H = 180, 64

    # Command side
    s.ellipse(30, 520, 140, 64, "client", "Traveller")
    s.box(220, 300, 170, H, "client", "API Gateway", "POST /trips")
    s.box(440, 300, 170, H, "lambda", "trip-commands", "Lambda")
    s.line([(100, 520), (100, 332), (220, 332)])
    s.text(160, 322, "book")
    s.line([(390, 332), (440, 332)])
    s.line([(610, 332), (720, 332)])
    s.text(665, 322, "StartExecution")

    s.box(720, 100, W, 440, "sfn", "Step Functions", "booking saga")
    for i, step in enumerate(["1. reserve flight", "2. reserve hotel", "3. charge payment", "4. confirm both", "", "on failure:", "release in reverse"]):
        s.text(810, 375 + i * 18, step, "sub", "middle")

    # Services: command queue -> Lambda -> circuit breaker -> vendor
    rows = [
        (140, "flight", "Flight service", "Airline"),
        (300, "hotel", "Hotel service", "Hotel provider"),
        (460, "payment", "Payment service", "Payment provider"),
    ]
    for top, queue, service, vendor in rows:
        cy = top + H / 2
        s.line([(900, cy), (1020, cy)])
        s.text(960, cy - 8, "command")
        s.box(1020, top, W, H, "sqs", "SQS", f"{queue} commands")
        s.line([(1110, top + H), (1110, top + 84)], dashed=True)
        s.box(1020, top + 84, W, 36, "dlq", "DLQ")
        s.line([(1200, cy), (1270, cy)])
        s.box(1270, top, W, H, "lambda", service, "Lambda")
        s.line([(1450, cy), (1480, cy)])
        s.box(1480, cy - 20, 80, 40, "cb", "breaker")
        s.line([(1560, cy), (1590, cy)])
        s.ellipse(1590, top, 180, H, "vendor", vendor, "vendor simulator")
        # The answer goes back to the saga with the task token.
        s.line([(1360, top), (1360, top - 20), (900, top - 20)], dashed=True)
        # Domain events leave under each row and join on the right.
        s.line([(1360, top + H), (1360, top + 92), (1810, top + 92)], arrow=False)
    s.text(1130, 113, "SendTaskSuccess / SendTaskFailure (task token)")
    s.line([(1810, 232), (1810, 640)])
    s.text(1585, 545, "FlightReserved, HotelReserved, PaymentCharged, …")

    # Event bus
    s.line([(810, 540), (810, 640)])
    s.text(820, 595, "TripRequested · TripConfirmed · TripCancelled", anchor="start")
    s.box(720, 640, 1110, H, "bus", "EventBridge", "trip-events bus")
    s.line([(720, 672), (610, 672)])
    s.text(665, 662, "archive")
    s.box(440, 640, 170, H, "ddb", "Event archive", "replay rebuilds the view")

    # Query side (CQRS)
    s.line([(1360, 704), (1360, 780)])
    s.text(1370, 748, "trip events", anchor="start")
    s.box(1270, 780, W, H, "sqs", "SQS", "projector")
    s.line([(1360, 844), (1360, 864)], dashed=True)
    s.box(1270, 864, W, 36, "dlq", "DLQ")
    s.line([(1270, 812), (1200, 812)])
    s.box(1020, 780, W, H, "lambda", "Projector", "Lambda")
    s.line([(1020, 812), (900, 812)])
    s.text(960, 802, "upsert")
    s.box(720, 780, W, H, "ddb", "DynamoDB", "trip-view (read model)")
    s.line([(610, 812), (720, 812)])
    s.text(665, 802, "read")
    s.box(440, 780, 170, H, "lambda", "trip-queries", "Lambda")
    s.line([(390, 812), (440, 812)])
    s.box(220, 780, 170, H, "client", "API Gateway", "GET /trips")
    s.line([(130, 581), (130, 812), (220, 812)])
    s.text(175, 802, "track")

    # Notifications: choreography for side effects
    s.line([(1520, 704), (1520, 972), (1450, 972)])
    s.text(1530, 760, "TripConfirmed / TripCancelled", anchor="start")
    s.box(1270, 940, W, H, "sqs", "SQS", "notification")
    s.line([(1360, 1004), (1360, 1024)], dashed=True)
    s.box(1270, 1024, W, 36, "dlq", "DLQ")
    s.line([(1270, 972), (1200, 972)])
    s.box(1020, 940, W, H, "lambda", "Notification", "Lambda")
    s.line([(1020, 972), (900, 972)])
    s.box(720, 940, W, H, "client", "SES", "email")
    s.line([(720, 972), (70, 972), (70, 581)])
    s.text(395, 962, "booked / cancelled")

    s.box(1590, 790, 240, H, "ddb", "DynamoDB", "circuit-breakers (shared state)")

    s.text(30, 1100, "Each service owns its own DynamoDB table (reservations, payments, notifications). "
           "Every SQS queue sends failed messages to its own dead-letter queue (dashed down).", "note", "start")
    s.text(30, 1122, "Dashed arrows back to Step Functions are task-token callbacks. "
           "Each breaker keeps its state in the circuit-breakers table, one item per vendor.", "note", "start")
    s.save("architecture.svg")


def saga():
    s = Svg(1480, 640, "The booking saga")
    W, H = 190, 70

    s.line([(915, 70), (915, 610)], dashed=True, arrow=False, color="#b8407f")
    s.text(40, 100, "Before the pivot: every step can be undone", "region", "start")
    s.text(940, 100, "After the pivot: only forward", "region", "start")

    s.dot(215, 130)
    s.line([(215, 138), (215, 170)])
    s.box(120, 170, W, H, "lambda", "Reserve flight", "hold a seat (15 min)")
    s.line([(310, 205), (400, 205)])
    s.text(355, 195, "reserved")
    s.box(400, 170, W, H, "lambda", "Reserve hotel", "hold a room (15 min)")
    s.line([(590, 205), (680, 205)])
    s.text(635, 195, "reserved")
    s.box(680, 170, W, H, "sfn", "Charge payment", "the pivot", thick=True)
    s.line([(870, 205), (960, 205)])
    s.text(892, 195, "charged")
    s.box(960, 170, W, H, "lambda", "Confirm flight + hotel", "in parallel")
    s.line([(1150, 205), (1240, 205)])
    s.text(1195, 195, "confirmed")
    s.box(1240, 170, W, H, "ok", "Trip confirmed", "TripConfirmed")

    # Failures and compensations
    s.box(120, 380, W, H, "bad", "Release flight", "compensation")
    s.box(400, 380, W, H, "bad", "Release hotel", "compensation")
    s.box(120, 520, W, H, "bad", "Trip cancelled", "TripCancelled + reason")
    s.box(960, 380, W, H, "warn", "Needs attention", "TripNeedsAttention + alarm")

    s.line([(120, 225), (80, 225), (80, 555), (120, 555)])
    s.text(88, 485, "no seat / vendor down", anchor="start")
    s.line([(450, 240), (450, 320), (250, 320), (250, 380)])
    s.text(350, 312, "no room / vendor down")
    s.line([(775, 240), (775, 415), (590, 415)])
    s.text(765, 330, "card declined / vendor down", anchor="end")
    s.line([(400, 415), (310, 415)])
    s.line([(215, 450), (215, 520)])
    s.line([(1055, 240), (1055, 380)])
    s.text(1065, 315, "retries exhausted", anchor="start")

    s.text(360, 540, "Each step retries VendorUnavailable with backoff (5, 10, 20 s) before failing.", "note", "start")
    s.text(360, 562, "Compensations are idempotent and retried too. Holds expire on their own,", "note", "start")
    s.text(360, 584, "so a saga that dies halfway still frees the seat and the room.", "note", "start")
    s.text(940, 540, "After the payment, confirmations are retried,", "note", "start")
    s.text(940, 562, "never compensated: a person decides.", "note", "start")
    s.save("saga.svg")


def circuit_breaker():
    s = Svg(1040, 470, "Circuit breaker, one per vendor")
    W, H = 240, 72

    s.box(80, 140, W, H, "ok", "Closed", "calls go through, failures counted")
    s.box(720, 140, W, H, "bad", "Open", "fail fast, vendor not called")
    s.box(400, 330, W, H, "warn", "Half-open", "one probe call, others fail fast")

    s.line([(320, 176), (720, 176)])
    s.text(520, 166, "5 failures within 60 s")
    s.text(520, 196, "(timeouts, connection errors, 5xx)")
    s.line([(860, 212), (860, 366), (640, 366)])
    s.text(750, 386, "after 30 s, one caller wins the probe")
    s.line([(600, 330), (600, 280), (800, 280), (800, 212)])
    s.text(700, 272, "probe fails")
    s.line([(400, 366), (200, 366), (200, 212)])
    s.text(300, 386, "probe succeeds")

    s.text(40, 432, "State is one DynamoDB item per vendor, shared by every Lambda instance; each transition is a conditional write.", "note", "start")
    s.text(40, 452, "A business refusal (no room, card declined) means the vendor is healthy: it doesn't count as a failure.", "note", "start")
    s.save("circuit-breaker.svg")


if __name__ == "__main__":
    architecture()
    saga()
    circuit_breaker()
