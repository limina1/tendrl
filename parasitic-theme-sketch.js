/**
 * Tendrl Parasitic Theme - p5.js Implementation
 *
 * Demonstrates the infection system with:
 * - Color transformation (host ⟷ parasite)
 * - Holographic logo glitch
 * - Growing vines
 * - Dripping ooze
 * - Screen tears
 * - Flash bursts
 */

// ===== THEME COLORS =====
const HOST = {
    bg: [239, 230, 220],      // #efe6dc warm beige
    text: [31, 41, 55],       // #1f2937 dark gray
    accent: [121, 92, 57],    // #795c39 brown
    border: [198, 168, 133]   // #c6a885 tan
};

const PARASITE = {
    bg: [10, 10, 10],         // #0a0a0a black
    text: [0, 255, 65],       // #00ff41 terminal green
    accent: [0, 255, 65],     // #00ff41 neon green
    border: [0, 255, 65]      // #00ff41 glowing green
};

const INFECTION_COLORS = {
    green: [0, 255, 65],
    purple: [157, 0, 255],
    cyan: [0, 247, 255],
    magenta: [255, 0, 247]
};

// ===== STATE =====
let infected = false;
let infectionIntensity = 0;
let targetIntensity = 0;
let infectionDuration = 0;
let recoveryTime = 0;
let nextInfectionTime = 0;
let autoInfect = true;
let lambda = 0.3;
let flickerSpeed = 3; // Hz

// ===== VISUAL ELEMENTS =====
let vines = [];
let oozeDrops = [];
let screenTears = [];
let flashAlpha = 0;
let logoGlitchOffset = 0;
let logoChromatic = { x: 0, y: 0 };

// ===== CARDS =====
let cards = [];

// ===== FONT =====
let monoFont;

function setup() {
    let canvas = createCanvas(1200, 800);
    canvas.parent('canvas-container');

    // Initialize cards
    for (let i = 0; i < 6; i++) {
        cards.push(new Card(
            100 + (i % 3) * 350,
            200 + Math.floor(i / 3) * 300,
            300,
            200
        ));
    }

    // Setup controls
    setupControls();

    // Schedule first infection
    scheduleNextInfection();
}

function draw() {
    // Blend background color based on infection intensity
    let bgColor = lerpColor(
        color(...HOST.bg),
        color(...PARASITE.bg),
        infectionIntensity
    );
    background(bgColor);

    // Update infection state
    updateInfection();

    // Draw vines (behind everything)
    if (infected && infectionIntensity > 0.2) {
        drawVines();
    }

    // Draw cards
    for (let card of cards) {
        card.display();
    }

    // Draw logo
    drawLogo();

    // Draw ooze (in front of cards)
    if (infected) {
        updateAndDrawOoze();
    }

    // Draw screen tears
    if (flashAlpha > 0) {
        drawFlash();
    }
    updateAndDrawTears();

    // Draw scanlines
    if (infected && infectionIntensity > 0.3) {
        drawScanlines();
    }

    // Update UI
    updateStatusDisplay();
}

// ===== INFECTION SYSTEM =====

function updateInfection() {
    // Smooth transition to target intensity
    if (infectionIntensity < targetIntensity) {
        infectionIntensity = lerp(infectionIntensity, targetIntensity, 0.05);
    } else if (infectionIntensity > targetIntensity) {
        infectionIntensity = lerp(infectionIntensity, targetIntensity, 0.02);
    }

    // Check for recovery
    if (infected && millis() > recoveryTime) {
        recover();
    }

    // Check for next infection
    if (autoInfect && !infected && millis() > nextInfectionTime) {
        triggerInfection();
    }
}

function triggerInfection() {
    infected = true;

    // Beta distribution for intensity (U-shaped - extremes preferred)
    targetIntensity = betaRandom(0.4, 0.5);

    // Gamma distribution for duration
    infectionDuration = gammaRandom(3.0, 100);
    recoveryTime = millis() + infectionDuration;

    // Visual effects
    createFlash();
    createScreenTears(5);
    spawnVines();
    spawnOoze(10);

    // Schedule next
    scheduleNextInfection();
}

function recover() {
    infected = false;
    targetIntensity = 0;
    vines = [];
}

function scheduleNextInfection() {
    // Exponential distribution
    let interval = -Math.log(Math.random()) / lambda * 10000; // ms
    nextInfectionTime = millis() + interval;
}

// ===== STATISTICAL DISTRIBUTIONS =====

function betaRandom(alpha, beta) {
    // Beta distribution using rejection sampling
    let x1 = Math.pow(Math.random(), 1.0 / alpha);
    let x2 = Math.pow(Math.random(), 1.0 / beta);
    return x1 / (x1 + x2);
}

function gammaRandom(shape, scale) {
    // Simplified gamma using sum of exponentials
    let sum = 0;
    for (let i = 0; i < shape; i++) {
        sum -= Math.log(Math.random());
    }
    return sum * scale;
}

// ===== LOGO RENDERING =====

function drawLogo() {
    push();

    let logoX = width / 2;
    let logoY = 80;

    textAlign(CENTER, CENTER);
    textSize(48);
    textFont('Courier New');

    if (infected && infectionIntensity > 0.1) {
        // Parasite logo (holographic glitch)

        // Flicker offset
        if (frameCount % (60 / max(flickerSpeed, 1)) < 5) {
            logoGlitchOffset = random(-3, 3);
            logoChromatic.x = random(-2, 2);
            logoChromatic.y = random(-1, 1);
        }

        // Chromatic aberration layers
        // Cyan layer (top half)
        fill(INFECTION_COLORS.cyan[0], INFECTION_COLORS.cyan[1], INFECTION_COLORS.cyan[2], 180);
        text('Tendrl', logoX + logoGlitchOffset + logoChromatic.x - 2, logoY);

        // Magenta layer (bottom half)
        fill(INFECTION_COLORS.magenta[0], INFECTION_COLORS.magenta[1], INFECTION_COLORS.magenta[2], 180);
        text('Tendrl', logoX + logoGlitchOffset + logoChromatic.x + 2, logoY);

        // Main green layer
        fill(...PARASITE.text);
        drawingContext.shadowBlur = 15;
        drawingContext.shadowColor = `rgba(0, 255, 65, ${infectionIntensity})`;
        text('Tendrl', logoX + logoGlitchOffset + 3, logoY);
        drawingContext.shadowBlur = 0;

        // Host layer (faded)
        fill(...HOST.text, 255 * (1 - infectionIntensity) * 0.7);
        text('Alexandria', logoX, logoY);

    } else {
        // Host logo
        fill(...HOST.text);
        text('Alexandria', logoX, logoY);
    }

    // Subtitle
    textSize(14);
    let subtitleColor = lerpColor(
        color(...HOST.text),
        color(...PARASITE.text),
        infectionIntensity
    );
    fill(subtitleColor);
    text('[local]', logoX, logoY + 35);

    pop();
}

// ===== VINES =====

function spawnVines() {
    // Left vine
    vines.push(new Vine('left'));
    // Right vine
    vines.push(new Vine('right'));
}

function drawVines() {
    for (let vine of vines) {
        vine.grow();
        vine.display();
    }
}

class Vine {
    constructor(side) {
        this.side = side;
        this.points = [];
        this.maxPoints = 15;
        this.growth = 0;
        this.breathe = 0;

        // Generate vine path
        let startX = side === 'left' ? 30 : width - 30;
        let startY = 0;

        for (let i = 0; i < this.maxPoints; i++) {
            let t = i / this.maxPoints;
            let x = startX + (side === 'left' ? 1 : -1) * sin(t * PI * 3) * 40;
            let y = startY + t * height;
            this.points.push({ x, y, size: random(3, 8) });
        }
    }

    grow() {
        if (this.growth < 1) {
            this.growth += 0.008; // 8 seconds to full growth
        }
        this.breathe += 0.02;
    }

    display() {
        let visiblePoints = floor(this.points.length * this.growth);

        push();

        // Breathing glow
        let glowSize = 8 + sin(this.breathe) * 4;

        // Draw path
        noFill();
        strokeWeight(2 + sin(this.breathe) * 0.5);
        stroke(...INFECTION_COLORS.green, 200 * infectionIntensity);
        drawingContext.shadowBlur = glowSize;
        drawingContext.shadowColor = `rgba(0, 255, 65, ${0.6 * infectionIntensity})`;

        beginShape();
        for (let i = 0; i < visiblePoints; i++) {
            let p = this.points[i];
            curveVertex(p.x, p.y);
        }
        endShape();

        // Draw nodes
        for (let i = 0; i < visiblePoints; i++) {
            let p = this.points[i];
            let pulse = (sin(this.breathe + i * 0.3) + 1) / 2;

            fill(...INFECTION_COLORS.green, 150 + pulse * 105);
            drawingContext.shadowBlur = 10 + pulse * 8;
            circle(p.x, p.y, p.size);
        }

        drawingContext.shadowBlur = 0;
        pop();
    }
}

// ===== OOZE =====

function spawnOoze(count) {
    for (let i = 0; i < count; i++) {
        setTimeout(() => {
            oozeDrops.push(new OozeDrop());
        }, i * random(100, 500));
    }
}

function updateAndDrawOoze() {
    for (let i = oozeDrops.length - 1; i >= 0; i--) {
        let drop = oozeDrops[i];
        drop.update();
        drop.display();

        if (drop.isDead()) {
            oozeDrops.splice(i, 1);
        }
    }
}

class OozeDrop {
    constructor() {
        this.x = random(width);
        this.startY = random(-50, 100);
        this.y = this.startY;
        this.length = random(100, 400);
        this.speed = random(2, 5);
        this.duration = random(1500, 3500);
        this.startTime = millis();
        this.colorIndex = floor(random(4));
        this.colors = [
            INFECTION_COLORS.green,
            INFECTION_COLORS.purple,
            INFECTION_COLORS.cyan,
            INFECTION_COLORS.magenta
        ];
    }

    update() {
        this.y += this.speed;
    }

    display() {
        let elapsed = millis() - this.startTime;
        let alpha = 255 * (1 - elapsed / this.duration);

        if (alpha > 0) {
            push();
            strokeWeight(4);

            // Gradient from color to transparent
            for (let i = 0; i < this.length; i++) {
                let t = i / this.length;
                let c = this.colors[this.colorIndex];
                stroke(c[0], c[1], c[2], alpha * (1 - t));
                point(this.x, this.y + i);
            }

            pop();
        }
    }

    isDead() {
        return millis() - this.startTime > this.duration;
    }
}

// ===== SCREEN TEARS =====

function createScreenTears(count) {
    for (let i = 0; i < count; i++) {
        setTimeout(() => {
            screenTears.push(new ScreenTear());
        }, i * 20);
    }
}

function updateAndDrawTears() {
    for (let i = screenTears.length - 1; i >= 0; i--) {
        let tear = screenTears[i];
        tear.update();
        tear.display();

        if (tear.isDead()) {
            screenTears.splice(i, 1);
        }
    }
}

class ScreenTear {
    constructor() {
        this.type = random(['horizontal', 'vertical', 'diagonal']);
        this.pos = random(height);
        this.offset = 0;
        this.duration = 60;
        this.startTime = millis();
    }

    update() {
        let t = (millis() - this.startTime) / this.duration;
        this.offset = sin(t * PI) * 20;
    }

    display() {
        let elapsed = millis() - this.startTime;
        if (elapsed > this.duration) return;

        push();

        if (this.type === 'horizontal') {
            strokeWeight(8);
            for (let x = 0; x < width; x += 20) {
                let t = x / width;
                let c = this.getRainbowColor(t);
                stroke(c[0], c[1], c[2], 200);
                line(x, this.pos + this.offset, x + 15, this.pos + this.offset);
            }
        } else if (this.type === 'vertical') {
            strokeWeight(8);
            for (let y = 0; y < height; y += 20) {
                let t = y / height;
                let c = this.getRainbowColor(t);
                stroke(c[0], c[1], c[2], 200);
                line(this.pos + this.offset, y, this.pos + this.offset, y + 15);
            }
        } else {
            strokeWeight(8);
            for (let i = 0; i < width; i += 20) {
                let t = i / width;
                let c = this.getRainbowColor(t);
                stroke(c[0], c[1], c[2], 200);
                let x1 = i;
                let y1 = i / 2 + this.offset;
                let x2 = i + 15;
                let y2 = (i + 15) / 2 + this.offset;
                line(x1, y1, x2, y2);
            }
        }

        pop();
    }

    getRainbowColor(t) {
        // Cycle through infection colors
        if (t < 0.25) return INFECTION_COLORS.green;
        if (t < 0.5) return INFECTION_COLORS.cyan;
        if (t < 0.75) return INFECTION_COLORS.purple;
        return INFECTION_COLORS.magenta;
    }

    isDead() {
        return millis() - this.startTime > this.duration;
    }
}

// ===== FLASH =====

function createFlash() {
    flashAlpha = 255;
}

function drawFlash() {
    push();

    // Cycle through colors
    let t = (255 - flashAlpha) / 255;
    let c;
    if (t < 0.33) c = INFECTION_COLORS.green;
    else if (t < 0.66) c = INFECTION_COLORS.purple;
    else c = INFECTION_COLORS.cyan;

    fill(c[0], c[1], c[2], flashAlpha * 0.5);
    rect(0, 0, width, height);

    flashAlpha *= 0.85; // Decay
    if (flashAlpha < 1) flashAlpha = 0;

    pop();
}

// ===== SCANLINES =====

function drawScanlines() {
    push();
    stroke(0, 255, 65, 8 * infectionIntensity);
    strokeWeight(1);

    let offset = (frameCount * 0.5) % 4;
    for (let y = 0; y < height; y += 4) {
        line(0, y + offset, width, y + offset);
    }

    pop();
}

// ===== CARDS =====

class Card {
    constructor(x, y, w, h) {
        this.x = x;
        this.y = y;
        this.w = w;
        this.h = h;
        this.infected = false;
        this.infectIntensity = 0;
    }

    display() {
        push();

        // Card background
        let cardBg = lerpColor(
            color(...HOST.bg, 240),
            color(10, 15, 10, 240),
            max(infectionIntensity, this.infectIntensity)
        );
        fill(cardBg);

        // Border
        let borderColor = lerpColor(
            color(...HOST.border),
            color(...PARASITE.border),
            max(infectionIntensity, this.infectIntensity)
        );
        stroke(borderColor);
        strokeWeight(2);

        // Glow when infected
        if (this.infectIntensity > 0.3) {
            drawingContext.shadowBlur = 15 * this.infectIntensity;
            drawingContext.shadowColor = `rgba(0, 255, 65, ${this.infectIntensity * 0.5})`;
        }

        rect(this.x, this.y, this.w, this.h, 8);
        drawingContext.shadowBlur = 0;

        // Left accent border
        let accentColor = lerpColor(
            color(...HOST.accent),
            color(...INFECTION_COLORS.green),
            max(infectionIntensity, this.infectIntensity)
        );
        stroke(accentColor);
        strokeWeight(4);
        line(this.x, this.y + 5, this.x, this.y + this.h - 5);

        // Content
        noStroke();
        let textColor = lerpColor(
            color(...HOST.text),
            color(...PARASITE.text),
            max(infectionIntensity, this.infectIntensity)
        );
        fill(textColor);

        textAlign(LEFT, TOP);
        textSize(16);
        text('Note Content', this.x + 20, this.y + 20);

        textSize(12);
        fill(...HOST.text, 150);
        text('Author Name', this.x + 20, this.y + 50);
        text('2h ago', this.x + 20, this.y + 70);

        // Stats
        textSize(14);
        text('💬 7  ❤️ 42  ⚡ 21k', this.x + 20, this.y + this.h - 30);

        pop();

        // Decay card infection
        if (this.infectIntensity > 0) {
            this.infectIntensity *= 0.95;
        }
    }

    contains(x, y) {
        return x > this.x && x < this.x + this.w &&
               y > this.y && y < this.y + this.h;
    }

    tryInfect() {
        // 15% chance
        if (random() < 0.15) {
            this.infected = true;
            this.infectIntensity = 0.8;

            // Spawn local effects
            for (let i = 0; i < 3; i++) {
                oozeDrops.push(new LocalOozeDrop(this.x + random(this.w), this.y));
            }
        }
    }
}

class LocalOozeDrop extends OozeDrop {
    constructor(x, y) {
        super();
        this.x = x;
        this.startY = y;
        this.y = y;
        this.length = random(50, 150);
        this.duration = 1000;
    }
}

// ===== CONTROLS =====

function setupControls() {
    // Infection level slider
    document.getElementById('infection-level').addEventListener('input', (e) => {
        let level = parseInt(e.target.value);
        document.getElementById('level-value').textContent = level;
        lambda = 0.05 + (level / 100) * 0.75;

        if (level === 0) {
            autoInfect = false;
            recover();
        } else if (level === 100) {
            autoInfect = false;
            targetIntensity = 0.85;
            infected = true;
            infectionDuration = Infinity;
        } else {
            autoInfect = true;
        }
    });

    // Lambda slider
    document.getElementById('lambda').addEventListener('input', (e) => {
        lambda = parseFloat(e.target.value);
        document.getElementById('lambda-value').textContent = lambda.toFixed(2);
    });

    // Flicker speed
    document.getElementById('flicker-speed').addEventListener('input', (e) => {
        flickerSpeed = parseInt(e.target.value);
        document.getElementById('flicker-value').textContent = flickerSpeed;
    });

    // Trigger button
    document.getElementById('trigger-infection').addEventListener('click', () => {
        triggerInfection();
    });

    // Auto toggle
    document.getElementById('toggle-auto').addEventListener('click', (e) => {
        autoInfect = !autoInfect;
        e.target.textContent = autoInfect ? '🔄 Auto: ON' : '⏸️ Auto: OFF';
    });
}

function updateStatusDisplay() {
    document.getElementById('state').textContent = infected ? 'INFECTED' : 'HOST';
    document.getElementById('intensity').textContent = infectionIntensity.toFixed(2);

    if (autoInfect && !infected) {
        let timeUntil = (nextInfectionTime - millis()) / 1000;
        document.getElementById('next').textContent = timeUntil > 0 ? timeUntil.toFixed(1) + 's' : 'now';
    } else {
        document.getElementById('next').textContent = '--';
    }
}

// ===== MOUSE INTERACTION =====

function mousePressed() {
    // Check if clicking on a card
    for (let card of cards) {
        if (card.contains(mouseX, mouseY)) {
            card.tryInfect();
        }
    }
}
