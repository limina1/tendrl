/**
 * Tendrl Parasitic Theme Engine
 *
 * Creates random "infection" events that transition the UI between
 * host (calm, leather aesthetic) and parasite (terminal green) states.
 *
 * Uses statistical distributions for realistic timing:
 * - Interval between infections: Exponential/Poisson/Uniform
 * - Infection intensity: Beta distribution (U-shaped/bimodal)
 * - Infection duration: Gamma distribution
 *
 * Usage:
 *   const parasite = new ParasiticTheme();
 *   // Or with custom config:
 *   const parasite = new ParasiticTheme({
 *     lambda: 0.3,
 *     autoGlitch: true,
 *     intensityAlpha: 0.4,
 *     intensityBeta: 0.5
 *   });
 */

console.log('🎭 parasitic-theme.js loaded');

class ParasiticTheme {
    constructor(config = {}) {
        console.log('🎭 ParasiticTheme constructor called with config:', config);
        // Interval distribution configuration
        this.lambda = config.lambda ?? 0.3;
        this.distribution = config.distribution ?? 'exponential';
        this.autoGlitch = config.autoGlitch ?? true;

        // Intensity distribution (Beta - bimodal U-shaped)
        // alpha, beta < 1: clusters at extremes (mostly healthy OR mostly infected)
        this.intensityAlpha = config.intensityAlpha ?? 0.4;
        this.intensityBeta = config.intensityBeta ?? 0.5;

        // Duration distribution (Gamma)
        this.durationMean = config.durationMean ?? 300;
        this.durationShape = config.durationShape ?? 3.0;

        // Visual effects
        this.logoGlitchSpeed = config.logoGlitchSpeed ?? 3; // Hz (default 3fps)

        // Card hover infection chance (0-1)
        this.hoverInfectionChance = config.hoverInfectionChance ?? 0.15;

        // State
        this.isInfected = false;
        this.currentIntensity = 0;
        this.currentDuration = 0;
        this.nextGlitchTime = 0;
        this.glitchTimeout = null;
        this.recoveryTimeout = null;
        this.continuousEffectsIntervals = []; // Track continuous effect timers

        // DOM elements (will be populated in init)
        this.body = null;
        this.glitchFlash = null;
        this.screenTear = null;
        this.statusElements = null;

        // Event callbacks
        this.onInfect = config.onInfect ?? null;
        this.onRecover = config.onRecover ?? null;

        this.init();
    }

    init() {
        console.log('🎭 ParasiticTheme.init() called');
        this.body = document.body;
        this.body.classList.add('parasitic-theme');

        // Load saved settings from localStorage
        this.loadSettings();
        console.log('🎭 Settings loaded. autoGlitch:', this.autoGlitch, 'lambda:', this.lambda);

        // Create overlay elements if they don't exist
        this.createOverlays();

        // Get status display elements
        this.statusElements = {
            state: document.getElementById('parasitic-state'),
            duration: document.getElementById('parasitic-duration'),
            next: document.getElementById('parasitic-next'),
            lambda: document.getElementById('parasitic-lambda')
        };

        // Setup controls if present
        this.setupControls();

        // Setup card hover effects
        this.setupCardHover();

        // Start auto-glitch loop
        if (this.autoGlitch) {
            this.scheduleNextGlitch();
        }

        // Start status updates
        this.startStatusUpdates();
    }

    /**
     * Load settings from localStorage
     */
    loadSettings() {
        const saved = localStorage.getItem('tendrl-infection-settings');
        if (saved) {
            try {
                const settings = JSON.parse(saved);
                console.log('💾 Loaded settings from localStorage:', settings);

                this.lambda = settings.lambda ?? this.lambda;
                this.distribution = settings.distribution ?? this.distribution;
                this.intensityAlpha = settings.intensityAlpha ?? this.intensityAlpha;
                this.intensityBeta = settings.intensityBeta ?? this.intensityBeta;
                this.durationMean = settings.durationMean ?? this.durationMean;
                this.durationShape = settings.durationShape ?? this.durationShape;
                this.autoGlitch = settings.autoGlitch ?? this.autoGlitch;
                this.logoGlitchSpeed = settings.logoGlitchSpeed ?? this.logoGlitchSpeed;

                // Restore infection level and apply state
                if (settings.infectionLevel !== undefined) {
                    const level = settings.infectionLevel;
                    console.log(`💾 Restoring infection level: ${level}%`);

                    if (level === 100) {
                        // Restore permanent infection state
                        console.log('%c🔒 PERMANENT INFECTION MODE (100%)', 'color: #ff00f7; font-weight: bold; font-size: 14px');
                        console.log('%cNo cycling, no drips, no effects. Move slider to 1-99% for dynamic effects.', 'color: #00ff41');
                        this.isInfected = true;
                        this.currentIntensity = 0.85;
                        this.currentDuration = Infinity;
                        this.body.classList.add('infected');
                        this.body.style.setProperty('--infection-intensity', this.currentIntensity);
                    } else if (level === 0) {
                        // Ensure clean Alexandria state - NO EFFECTS
                        console.log('%c✨ PURE ALEXANDRIA MODE (0%)', 'color: #c6a885; font-weight: bold; font-size: 14px');
                        console.log('%cStatic warm leather theme. No infections, no drips, no cycling.', 'color: #795c39');
                        this.autoGlitch = false; // Disable auto-glitch
                        this.isInfected = false;
                        this.body.classList.remove('infected');
                        clearTimeout(this.glitchTimeout);
                        clearTimeout(this.recoveryTimeout);
                    } else {
                        // For levels 1-99, DO NOT restore infection state
                        // Let auto-glitch cycle naturally
                        console.log(`%c🔄 DYNAMIC MODE (${level}%)`, 'color: #00ff41; font-weight: bold; font-size: 14px');
                        console.log('%cAuto-cycling enabled. Watch for infections!', 'color: #00cc33');
                        this.isInfected = false;
                        this.body.classList.remove('infected');
                    }
                }
            } catch (e) {
                console.warn('Failed to load settings:', e);
            }
        } else {
            console.log('💾 No saved settings found, using defaults');
        }
    }

    /**
     * Save settings to localStorage
     */
    saveSettings() {
        // Calculate current infection level from state
        let infectionLevel = 30; // default
        if (!this.autoGlitch && !this.isInfected) {
            infectionLevel = 0;
        } else if (this.isInfected && this.currentDuration === Infinity) {
            infectionLevel = 100;
        } else if (this.autoGlitch) {
            // Reverse mapping from lambda
            infectionLevel = Math.round((this.lambda - 0.05) / 0.75 * 100);
        }

        const settings = {
            lambda: this.lambda,
            distribution: this.distribution,
            intensityAlpha: this.intensityAlpha,
            intensityBeta: this.intensityBeta,
            durationMean: this.durationMean,
            durationShape: this.durationShape,
            autoGlitch: this.autoGlitch,
            logoGlitchSpeed: this.logoGlitchSpeed,
            infectionLevel: infectionLevel
        };
        localStorage.setItem('tendrl-infection-settings', JSON.stringify(settings));
    }

    /**
     * Map infection level (0-100) to all parameters
     * 0% = Pure Alexandria (static warm leather, NO effects, NO cycling)
     * 1-99% = Dynamic Mode (cycling infections, drips, vines, effects)
     * 100% = Full Tendrl Takeover (static terminal green, NO cycling)
     */
    applyInfectionLevel(level) {
        const normalized = level / 100; // 0 to 1

        if (level === 0) {
            // Pure Alexandria - disable auto-glitch entirely
            this.autoGlitch = false;
            clearTimeout(this.glitchTimeout);
            this.recover(); // Force recovery to Alexandria state
            this.saveSettings();

            // Update checkbox
            const autoGlitchCheckbox = document.getElementById('autoGlitch');
            if (autoGlitchCheckbox) autoGlitchCheckbox.checked = false;

        } else if (level === 100) {
            // Full Tendrl Takeover - permanent infection, no cycling
            this.autoGlitch = false;
            clearTimeout(this.glitchTimeout);
            clearTimeout(this.recoveryTimeout);

            // Force permanent infection at high intensity
            this.currentIntensity = 0.85; // Strong but not overwhelming
            this.currentDuration = Infinity; // Never recover
            this.isInfected = true;
            this.body.classList.add('infected');
            this.body.style.setProperty('--infection-intensity', this.currentIntensity);

            this.saveSettings();

            // Update checkbox
            const autoGlitchCheckbox = document.getElementById('autoGlitch');
            if (autoGlitchCheckbox) autoGlitchCheckbox.checked = false;

        } else {
            // 1-99%: Enable cycling with increasing frequency
            this.autoGlitch = true;

            // Frequency: 1% = very rare (lambda 0.05), 99% = very frequent (lambda 0.8)
            // (We stop before 1.0 to avoid too much chaos before full takeover)
            this.lambda = 0.05 + (normalized * 0.75);

            // Intensity: 1% = subtle, 99% = strong (but save extreme for 100%)
            this.intensityAlpha = 0.5 - normalized * 0.3;
            this.intensityBeta = 0.5 - normalized * 0.3;

            // Duration: 1% = brief (100ms), 99% = longer (600ms)
            this.durationMean = 100 + normalized * 500;
            this.durationShape = 3.0 - normalized * 1.0;

            this.saveSettings();

            // Update checkbox
            const autoGlitchCheckbox = document.getElementById('autoGlitch');
            if (autoGlitchCheckbox) autoGlitchCheckbox.checked = true;

            // Reschedule with new parameters
            if (!this.isInfected) {
                clearTimeout(this.glitchTimeout);
                this.scheduleNextGlitch();
            }
        }
    }

    createOverlays() {
        // Scanlines overlay
        if (!document.querySelector('.infection-overlay.scanlines')) {
            const scanlines = document.createElement('div');
            scanlines.className = 'infection-overlay scanlines';
            document.body.appendChild(scanlines);
        }

        // Glitch flash
        if (!document.getElementById('glitchFlash')) {
            const flash = document.createElement('div');
            flash.id = 'glitchFlash';
            flash.className = 'glitch-flash';
            document.body.appendChild(flash);
        }
        this.glitchFlash = document.getElementById('glitchFlash');

        // Screen tear
        if (!document.getElementById('screenTear')) {
            const tear = document.createElement('div');
            tear.id = 'screenTear';
            tear.className = 'screen-tear';
            document.body.appendChild(tear);
        }
        this.screenTear = document.getElementById('screenTear');

        // Vines (optional decorative element)
        if (!document.querySelector('.vine-container')) {
            const vines = document.createElement('div');
            vines.className = 'vine-container';
            vines.innerHTML = `
                <svg class="vine vine-left" viewBox="0 0 100 800" preserveAspectRatio="none">
                    <path d="M 10 0 Q 30 100, 15 200 Q 0 300, 25 400 Q 40 500, 10 600 Q -10 700, 20 800" />
                    <circle cx="15" cy="200" r="4" />
                    <circle cx="25" cy="400" r="4" />
                    <circle cx="10" cy="600" r="4" />
                    <path d="M 15 200 Q 50 180, 70 220" />
                    <circle cx="70" cy="220" r="3" />
                    <path d="M 25 400 Q 60 380, 80 420" />
                    <circle cx="80" cy="420" r="3" />
                </svg>
                <svg class="vine vine-right" viewBox="0 0 100 800" preserveAspectRatio="none">
                    <path d="M 10 0 Q 30 100, 15 200 Q 0 300, 25 400 Q 40 500, 10 600 Q -10 700, 20 800" />
                    <circle cx="15" cy="200" r="4" />
                    <circle cx="25" cy="400" r="4" />
                    <circle cx="10" cy="600" r="4" />
                </svg>
            `;
            document.body.appendChild(vines);
        }
    }

    // ========================================
    // DISTRIBUTIONS
    // ========================================

    /**
     * Exponential distribution - time between events in Poisson process
     */
    exponentialRandom() {
        return -Math.log(1 - Math.random()) / this.lambda;
    }

    /**
     * Poisson-like interval (sum of exponentials)
     */
    poissonRandom() {
        let sum = 0;
        for (let i = 0; i < 3; i++) {
            sum += -Math.log(1 - Math.random());
        }
        return sum / (3 * this.lambda);
    }

    /**
     * Uniform distribution between 1-9 seconds
     */
    uniformRandom() {
        return 1 + Math.random() * 8;
    }

    /**
     * Get next interval based on selected distribution
     */
    getNextInterval() {
        let interval;
        switch (this.distribution) {
            case 'exponential':
                interval = this.exponentialRandom();
                break;
            case 'poisson':
                interval = this.poissonRandom();
                break;
            case 'uniform':
                interval = this.uniformRandom();
                break;
            default:
                interval = this.exponentialRandom();
        }
        // Clamp to reasonable range: 0.3s - 30s
        return Math.max(0.3, Math.min(30, interval));
    }

    /**
     * Gamma distribution using Marsaglia and Tsang's method
     * Used for duration sampling
     */
    gammaRandom(shape, scale) {
        if (shape < 1) {
            return this.gammaRandom(shape + 1, scale) * Math.pow(Math.random(), 1 / shape);
        }

        const d = shape - 1/3;
        const c = 1 / Math.sqrt(9 * d);

        while (true) {
            let x, v;
            do {
                // Box-Muller for standard normal
                const u1 = Math.random();
                const u2 = Math.random();
                x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
                v = 1 + c * x;
            } while (v <= 0);

            v = v * v * v;
            const u = Math.random();

            if (u < 1 - 0.0331 * (x * x) * (x * x)) {
                return d * v * scale;
            }

            if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) {
                return d * v * scale;
            }
        }
    }

    /**
     * Beta distribution using Gamma variates
     * When alpha, beta < 1: U-shaped (bimodal at 0 and 1)
     */
    betaRandom(alpha, beta) {
        const gammaAlpha = this.gammaRandom(alpha, 1);
        const gammaBeta = this.gammaRandom(beta, 1);
        return gammaAlpha / (gammaAlpha + gammaBeta);
    }

    /**
     * Get random intensity using Beta distribution
     */
    getRandomIntensity() {
        const raw = this.betaRandom(this.intensityAlpha, this.intensityBeta);
        // Clamp to [0.05, 1] to avoid completely invisible glitches
        return Math.max(0.05, Math.min(1.0, raw));
    }

    /**
     * Get random duration using Gamma distribution
     */
    getRandomDuration() {
        const scale = this.durationMean / this.durationShape;
        const duration = this.gammaRandom(this.durationShape, scale);
        // Clamp to 50ms - 3000ms
        return Math.max(50, Math.min(3000, duration));
    }

    // ========================================
    // INFECTION CONTROL
    // ========================================

    /**
     * Schedule the next automatic glitch
     */
    scheduleNextGlitch() {
        if (!this.autoGlitch) {
            console.log('⏸️ scheduleNextGlitch: autoGlitch is OFF, not scheduling');
            return;
        }

        const interval = this.getNextInterval();
        this.nextGlitchTime = Date.now() + interval * 1000;

        console.log(`⏰ Next infection scheduled in ${interval.toFixed(2)}s`);

        this.glitchTimeout = setTimeout(() => {
            console.log('⚡ Auto-infection triggered!');
            this.triggerInfection();
            this.scheduleNextGlitch();
        }, interval * 1000);
    }

    /**
     * Reschedule next glitch if auto-glitch is enabled and not currently infected
     * Useful when settings change to avoid long waits
     */
    rescheduleIfNeeded() {
        if (this.autoGlitch && !this.isInfected) {
            clearTimeout(this.glitchTimeout);
            this.scheduleNextGlitch();
        }
    }

    /**
     * Trigger an infection event
     * @param {number|null} forcedIntensity - Override random intensity
     * @param {number|null} forcedDuration - Override random duration (ms)
     */
    triggerInfection(forcedIntensity = null, forcedDuration = null) {
        if (this.isInfected) {
            console.log('🦠 triggerInfection: Already infected, skipping');
            return;
        }

        // Get random or forced values
        this.currentIntensity = forcedIntensity !== null
            ? forcedIntensity
            : this.getRandomIntensity();

        this.currentDuration = forcedDuration !== null
            ? forcedDuration
            : this.getRandomDuration();

        console.log(`🦠 INFECTION TRIGGERED! Intensity: ${this.currentIntensity.toFixed(2)}, Duration: ${this.currentDuration}ms`);

        // Set CSS custom property for intensity
        this.body.style.setProperty('--infection-intensity', this.currentIntensity);

        // Add infected class
        this.isInfected = true;
        this.body.classList.add('infected');

        // Visual effects based on intensity
        if (this.currentIntensity > 0.2) {
            this.flashEffect();
        }

        if (this.currentIntensity > 0.4 && Math.random() > 0.5) {
            this.tearEffect();
        }

        // Start continuous effects while infected
        this.startContinuousEffects();

        if (this.currentIntensity > 0.7 && Math.random() > 0.4) {
            this.cascadeSpikeEffect();
        }

        // Callback
        if (this.onInfect) {
            this.onInfect(this.currentIntensity, this.currentDuration);
        }

        // Schedule recovery
        clearTimeout(this.recoveryTimeout);
        this.recoveryTimeout = setTimeout(() => {
            this.recover();
        }, this.currentDuration);
    }

    /**
     * Start continuous ambient effects while infected (vines growing, ooze dripping)
     */
    startContinuousEffects() {
        console.log(`🦠 Starting continuous effects (intensity: ${this.currentIntensity})`);

        // Clear any existing intervals
        this.stopContinuousEffects();

        // Initial burst
        if (this.currentIntensity > 0.3) {
            console.log('🌊 Initial drip burst');
            this.dripEffect();
        }
        if (this.currentIntensity > 0.4 && Math.random() > 0.5) {
            console.log('🌿 Initial vine burst');
            this.drawVines();
        }

        // Schedule stochastic dripping (random timing)
        this.scheduleNextDrip();

        // Schedule stochastic vine growth (random timing)
        this.scheduleNextVine();
    }

    /**
     * Schedule next drip with random interval
     */
    scheduleNextDrip() {
        if (!this.isInfected) {
            console.log('💧 scheduleNextDrip: Not infected, skipping');
            return;
        }

        // Random interval based on intensity: 0.5-3s at high infection, 2-5s at low
        const minInterval = 500 + (1 - this.currentIntensity) * 1500; // 500-2000ms
        const maxInterval = 3000 + (1 - this.currentIntensity) * 2000; // 3000-5000ms
        const nextDripDelay = minInterval + Math.random() * (maxInterval - minInterval);

        console.log(`💧 Scheduling next drip in ${(nextDripDelay / 1000).toFixed(2)}s (intensity: ${this.currentIntensity})`);

        const dripTimer = setTimeout(() => {
            if (this.isInfected) {
                this.dripEffect();
                this.scheduleNextDrip(); // Schedule the next one
            }
        }, nextDripDelay);

        this.continuousEffectsIntervals.push(dripTimer);
    }

    /**
     * Schedule next vine growth with random interval
     */
    scheduleNextVine() {
        if (!this.isInfected) return;

        // Random interval based on intensity: 3-8s at high infection, 8-15s at low
        const minInterval = 3000 + (1 - this.currentIntensity) * 5000; // 3000-8000ms
        const maxInterval = 8000 + (1 - this.currentIntensity) * 7000; // 8000-15000ms
        const nextVineDelay = minInterval + Math.random() * (maxInterval - minInterval);

        const vineTimer = setTimeout(() => {
            if (this.isInfected && Math.random() > 0.2) { // 80% chance to spawn
                this.drawVines();
            }
            if (this.isInfected) {
                this.scheduleNextVine(); // Schedule the next one
            }
        }, nextVineDelay);

        this.continuousEffectsIntervals.push(vineTimer);
    }

    /**
     * Stop all continuous effects
     */
    stopContinuousEffects() {
        // Clear both intervals and timeouts
        this.continuousEffectsIntervals.forEach(timer => {
            clearInterval(timer);
            clearTimeout(timer);
        });
        this.continuousEffectsIntervals = [];
    }

    /**
     * Recover from infection
     */
    recover() {
        const wasIntensity = this.currentIntensity;

        this.isInfected = false;
        this.body.classList.remove('infected');
        this.currentIntensity = 0;

        // Stop continuous effects
        this.stopContinuousEffects();

        // Optional flash on recovery for high intensity
        if (wasIntensity > 0.7 && Math.random() > 0.5) {
            this.flashEffect();
        }

        // Callback
        if (this.onRecover) {
            this.onRecover();
        }
    }

    /**
     * Force full takeover (max intensity, long duration)
     */
    fullTakeover() {
        this.triggerInfection(1.0, 1500);
    }

    // ========================================
    // VISUAL EFFECTS
    // ========================================

    flashEffect() {
        if (!this.glitchFlash) return;
        this.glitchFlash.classList.add('active');
        setTimeout(() => this.glitchFlash.classList.remove('active'), 80);
    }

    tearEffect() {
        // Create a dynamic tear with random orientation
        const tear = document.createElement('div');
        tear.className = 'screen-tear';

        // Random orientation: horizontal, vertical, or diagonal
        const orientations = ['horizontal', 'vertical', 'diagonal'];
        const orientation = orientations[Math.floor(Math.random() * orientations.length)];
        tear.classList.add(orientation);

        // Random colors
        const colors = [
            'var(--infection-green)',
            'var(--infection-purple)',
            'var(--infection-cyan)',
            'var(--infection-magenta)'
        ];
        const primaryColor = colors[Math.floor(Math.random() * colors.length)];

        // Set position and rotation based on orientation
        if (orientation === 'horizontal') {
            tear.style.top = `${Math.random() * 100}%`;
            tear.style.setProperty('--tear-angle', '90deg');
        } else if (orientation === 'vertical') {
            tear.style.left = `${Math.random() * 100}%`;
            tear.style.setProperty('--tear-angle', '0deg');
        } else if (orientation === 'diagonal') {
            const rotation = Math.random() * 360;
            tear.style.top = `${Math.random() * 100}%`;
            tear.style.left = `${Math.random() * 100}%`;
            tear.style.setProperty('--tear-rotation', `${rotation}deg`);
            tear.style.setProperty('--tear-angle', `${rotation}deg`);
        }

        document.body.appendChild(tear);
        requestAnimationFrame(() => {
            tear.classList.add('active');
            setTimeout(() => {
                tear.classList.remove('active');
                setTimeout(() => tear.remove(), 100);
            }, 60);
        });
    }

    dripEffect() {
        // Create dripping ooze splattered across the entire screen
        const numDrips = 8 + Math.floor(Math.random() * 12); // 8-20 drips per burst (increased from 5-12)
        console.log(`🌊 DRIP EFFECT: Creating ${numDrips} drips`);

        for (let i = 0; i < numDrips; i++) {
            setTimeout(() => {
                const drip = document.createElement('div');
                drip.className = 'drip-ooze';

                // Multi-colored ooze palette
                const colors = [
                    'var(--infection-green)',
                    'var(--infection-purple)',
                    'var(--infection-cyan)',
                    'var(--infection-magenta)'
                ];
                const color = colors[Math.floor(Math.random() * colors.length)];

                // RANDOMNESS: Variable drip width (3-12px for more visibility)
                const dripWidth = 3 + Math.random() * 9;
                drip.style.width = `${dripWidth}px`;

                // SPLATTERED: Start from ANY position on screen (not just top)
                const startX = Math.random() * 100;
                const startY = Math.random() * 80; // Anywhere from top 80% of screen

                drip.style.left = `${startX}%`;
                drip.style.setProperty('--drip-start', `${startY}vh`);

                // MUCH LONGER: Variable drip length (300-1200px - 2x longer)
                const dripLength = 300 + Math.random() * 900;
                drip.style.setProperty('--drip-length', `${dripLength}px`);

                // Apply color
                drip.style.setProperty('--ooze-color', color);

                // FASTER FALL: 2-5 seconds to drip (was 4-8s, faster drip speed)
                const fallDuration = 2000 + Math.random() * 3000;
                drip.style.setProperty('--drip-duration', `${fallDuration}ms`);

                // RANDOMNESS: More drips have blur for depth
                if (Math.random() > 0.3) {
                    drip.style.filter = `blur(${0.5 + Math.random() * 2}px)`;
                }

                // RANDOMNESS: Some drips have stronger glow
                if (Math.random() > 0.6) {
                    drip.style.boxShadow = `0 0 ${5 + Math.random() * 10}px ${color}`;
                }

                document.body.appendChild(drip);
                requestAnimationFrame(() => {
                    drip.classList.add('active');
                    // LONGER VISIBILITY: Remove after fall + 3s fade
                    setTimeout(() => drip.remove(), fallDuration + 3000);
                });
            }, i * 100); // Faster cascade (was 150ms, now 100ms)
        }
    }

    /**
     * Update logo animation speed dynamically
     */
    updateLogoAnimationSpeed() {
        const duration = (1 / this.logoGlitchSpeed).toFixed(3); // Convert Hz to seconds

        // Update CSS custom property for animation duration
        document.documentElement.style.setProperty('--logo-flicker-duration', `${duration}s`);
    }

    cascadeSpikeEffect() {
        // Create cascading spikes at random angles
        const numSpikes = 3 + Math.floor(Math.random() * 5); // 3-7 spikes
        const centerX = Math.random() * window.innerWidth;
        const centerY = Math.random() * window.innerHeight;

        for (let i = 0; i < numSpikes; i++) {
            setTimeout(() => {
                const spike = document.createElement('div');
                spike.className = 'cascade-spike';

                const colors = [
                    'var(--infection-green)',
                    'var(--infection-purple)',
                    'var(--infection-cyan)',
                    'var(--infection-magenta)'
                ];
                const color = colors[Math.floor(Math.random() * colors.length)];

                const angle = (360 / numSpikes) * i + Math.random() * 30;
                const distance = 50 + Math.random() * 100;

                spike.style.left = `${centerX + Math.cos(angle * Math.PI / 180) * distance}px`;
                spike.style.top = `${centerY + Math.sin(angle * Math.PI / 180) * distance}px`;
                spike.style.setProperty('--spike-rotation', `${angle}deg`);
                spike.style.setProperty('--spike-color', color);

                document.body.appendChild(spike);
                requestAnimationFrame(() => {
                    spike.classList.add('active');
                    setTimeout(() => spike.remove(), 300);
                });
            }, i * 50);
        }
    }

    drawVines() {
        // Create dynamic vines that grow slowly from edges
        const sides = ['left', 'right', 'top'];
        const side = sides[Math.floor(Math.random() * sides.length)];

        const vineContainer = document.createElement('div');
        vineContainer.className = 'dynamic-vine-container';
        vineContainer.style.position = 'fixed';
        vineContainer.style.pointerEvents = 'none';
        vineContainer.style.zIndex = '50';
        vineContainer.style.opacity = '0';

        let svg, path, circles;
        const height = window.innerHeight;
        const width = 150;

        // RANDOMNESS: Vine length varies (30% to 100% of screen)
        const lengthFactor = 0.3 + Math.random() * 0.7;

        // RANDOMNESS: Variable segment count (2-8 segments for variety)
        const baseSegments = 2 + Math.floor(Math.random() * 7);

        // RANDOMNESS: Branching probability (20% to 70%)
        const branchProbability = 0.2 + Math.random() * 0.5;

        // RANDOMNESS: Growth speed (6-15 seconds)
        const growthDuration = 6 + Math.random() * 9;

        // RANDOMNESS: How long vine stays visible (8-20 seconds)
        const visibleDuration = 8 + Math.random() * 12;

        // RANDOMNESS: Fade out speed (1-3 seconds)
        const fadeOutDuration = 1 + Math.random() * 2;

        // Generate organic vine path with bezier curves
        const generateVinePath = () => {
            const actualHeight = height * lengthFactor;
            const segments = baseSegments;
            let pathData = 'M 30 0';
            let y = 0;
            let x = 30;
            let circlePositions = [];

            for (let i = 0; i < segments; i++) {
                const segmentHeight = actualHeight / segments;
                const nextY = y + segmentHeight;

                // RANDOMNESS: Wider horizontal wandering (0-80px)
                const nextX = 10 + Math.random() * 70;

                // RANDOMNESS: More aggressive control point deviation
                const cp1x = x + (Math.random() - 0.5) * 60;
                const cp1y = y + segmentHeight * (0.2 + Math.random() * 0.3);
                const cp2x = nextX + (Math.random() - 0.5) * 60;
                const cp2y = y + segmentHeight * (0.6 + Math.random() * 0.3);

                pathData += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${nextX} ${nextY}`;

                // Add node positions for flowers/leaves
                if (Math.random() > 0.5) {
                    // RANDOMNESS: Variable node sizes (2-6px radius)
                    circlePositions.push({ x: nextX, y: nextY, r: 2 + Math.random() * 4 });

                    // RANDOMNESS: More branching variation
                    if (Math.random() > (1 - branchProbability)) {
                        const branchLength = 20 + Math.random() * 50;
                        const branchAngle = (Math.random() - 0.5) * 60; // -30 to +30 degrees
                        const branchX = nextX + branchLength * Math.cos(branchAngle * Math.PI / 180);
                        const branchY = nextY + branchLength * Math.sin(branchAngle * Math.PI / 180);

                        // RANDOMNESS: Some branches have sub-branches
                        if (Math.random() > 0.7) {
                            const subBranchX = branchX + (Math.random() - 0.5) * 30;
                            const subBranchY = branchY + 20 + Math.random() * 20;
                            pathData += ` M ${nextX} ${nextY} Q ${nextX + 15} ${nextY + (Math.random() - 0.5) * 20}, ${branchX} ${branchY} Q ${branchX + 10} ${branchY + 10}, ${subBranchX} ${subBranchY}`;
                            circlePositions.push({ x: subBranchX, y: subBranchY, r: 1.5 + Math.random() * 2 });
                        } else {
                            pathData += ` M ${nextX} ${nextY} Q ${nextX + 20} ${nextY + (Math.random() - 0.5) * 15}, ${branchX} ${branchY}`;
                        }

                        circlePositions.push({ x: branchX, y: branchY, r: 2 + Math.random() * 2 });
                        pathData += ` M ${nextX} ${nextY}`;
                    }
                }

                y = nextY;
                x = nextX;
            }

            return { pathData, circlePositions };
        };

        const { pathData, circlePositions } = generateVinePath();

        // RANDOMNESS: Variable stroke width (3-7px - 2x thicker)
        const strokeWidth = 3 + Math.random() * 4;

        // Node appearance timing spread
        const nodeAppearDelay = growthDuration * 0.4; // Start appearing 40% through growth
        const nodeStagger = growthDuration * 0.05; // 5% of growth time between nodes

        // Create SVG based on side
        if (side === 'left') {
            vineContainer.style.left = '0';
            vineContainer.style.top = '0';
            vineContainer.style.width = `${width}px`;
            vineContainer.style.height = '100%';

            svg = `<svg class="dynamic-vine" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="width:100%; height:100%;">
                <path d="${pathData}" stroke="var(--infection-green)" stroke-width="${strokeWidth}" fill="none"
                      filter="drop-shadow(0 0 5px rgba(0, 255, 65, 0.6))"
                      stroke-dasharray="2000" stroke-dashoffset="2000"
                      style="animation: vine-draw-slow ${growthDuration}s ease-out forwards;" />`;

            circlePositions.forEach((pos, i) => {
                svg += `<circle cx="${pos.x}" cy="${pos.y}" r="${pos.r}"
                        fill="var(--infection-green)"
                        filter="drop-shadow(0 0 8px var(--infection-green))"
                        opacity="0"
                        style="animation: vine-node-appear 0.5s ease-out ${nodeAppearDelay + i * nodeStagger}s forwards, vine-node-pulse 3s ease-in-out infinite ${nodeAppearDelay + i * nodeStagger + 0.5}s;" />`;
            });

            svg += `</svg>`;
        } else if (side === 'right') {
            vineContainer.style.right = '0';
            vineContainer.style.top = '0';
            vineContainer.style.width = `${width}px`;
            vineContainer.style.height = '100%';

            svg = `<svg class="dynamic-vine" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="width:100%; height:100%; transform: scaleX(-1);">
                <path d="${pathData}" stroke="var(--infection-green)" stroke-width="${strokeWidth}" fill="none"
                      filter="drop-shadow(0 0 5px rgba(0, 255, 65, 0.6))"
                      stroke-dasharray="2000" stroke-dashoffset="2000"
                      style="animation: vine-draw-slow ${growthDuration}s ease-out forwards;" />`;

            circlePositions.forEach((pos, i) => {
                svg += `<circle cx="${pos.x}" cy="${pos.y}" r="${pos.r}"
                        fill="var(--infection-green)"
                        filter="drop-shadow(0 0 8px var(--infection-green))"
                        opacity="0"
                        style="animation: vine-node-appear 0.5s ease-out ${nodeAppearDelay + i * nodeStagger}s forwards, vine-node-pulse 3s ease-in-out infinite ${nodeAppearDelay + i * nodeStagger + 0.5}s;" />`;
            });

            svg += `</svg>`;
        } else if (side === 'top') {
            // Horizontal vine from top
            vineContainer.style.left = '0';
            vineContainer.style.top = '0';
            vineContainer.style.width = '100%';
            vineContainer.style.height = `${width}px`;

            const hPathData = this.generateHorizontalVinePath(window.innerWidth, width, lengthFactor, baseSegments, branchProbability);
            svg = `<svg class="dynamic-vine" viewBox="0 0 ${window.innerWidth} ${width}" preserveAspectRatio="none" style="width:100%; height:100%;">
                <path d="${hPathData.pathData}" stroke="var(--infection-green)" stroke-width="${strokeWidth}" fill="none"
                      filter="drop-shadow(0 0 5px rgba(0, 255, 65, 0.6))"
                      stroke-dasharray="2000" stroke-dashoffset="2000"
                      style="animation: vine-draw-slow ${growthDuration}s ease-out forwards;" />`;

            hPathData.circlePositions.forEach((pos, i) => {
                svg += `<circle cx="${pos.x}" cy="${pos.y}" r="${pos.r}"
                        fill="var(--infection-green)"
                        filter="drop-shadow(0 0 8px var(--infection-green))"
                        opacity="0"
                        style="animation: vine-node-appear 0.5s ease-out ${nodeAppearDelay + i * nodeStagger}s forwards, vine-node-pulse 3s ease-in-out infinite ${nodeAppearDelay + i * nodeStagger + 0.5}s;" />`;
            });

            svg += `</svg>`;
        }

        vineContainer.innerHTML = svg;
        document.body.appendChild(vineContainer);

        // Fade in the vine container
        requestAnimationFrame(() => {
            vineContainer.style.transition = 'opacity 1s ease-in';
            vineContainer.style.opacity = `${Math.min(this.currentIntensity, 0.8)}`;
        });

        // Lifecycle: Stay visible, then fade out and remove
        const totalLifespan = growthDuration * 1000 + visibleDuration * 1000;
        setTimeout(() => {
            vineContainer.style.transition = `opacity ${fadeOutDuration}s ease-out`;
            vineContainer.style.opacity = '0';
            setTimeout(() => vineContainer.remove(), fadeOutDuration * 1000);
        }, totalLifespan);
    }

    generateHorizontalVinePath(totalWidth, height, lengthFactor = 1, segments = 5, branchProbability = 0.4) {
        const actualWidth = totalWidth * lengthFactor;
        let pathData = 'M 0 30';
        let x = 0;
        let y = 30;
        let circlePositions = [];

        for (let i = 0; i < segments; i++) {
            const segmentWidth = actualWidth / segments;
            const nextX = x + segmentWidth;

            // RANDOMNESS: More vertical wandering (10-80px)
            const nextY = 10 + Math.random() * 70;

            // RANDOMNESS: More aggressive control point deviation
            const cp1x = x + segmentWidth * (0.2 + Math.random() * 0.3);
            const cp1y = y + (Math.random() - 0.5) * 50;
            const cp2x = x + segmentWidth * (0.6 + Math.random() * 0.3);
            const cp2y = nextY + (Math.random() - 0.5) * 50;

            pathData += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${nextX} ${nextY}`;

            // Add nodes
            if (Math.random() > 0.5) {
                // RANDOMNESS: Variable node sizes
                circlePositions.push({ x: nextX, y: nextY, r: 2 + Math.random() * 4 });

                // Add branches
                if (Math.random() > (1 - branchProbability)) {
                    const branchLength = 20 + Math.random() * 50;
                    const branchAngle = 90 + (Math.random() - 0.5) * 60; // Downward branches
                    const branchX = nextX + branchLength * Math.cos(branchAngle * Math.PI / 180);
                    const branchY = nextY + branchLength * Math.sin(branchAngle * Math.PI / 180);

                    pathData += ` M ${nextX} ${nextY} Q ${nextX + (Math.random() - 0.5) * 20} ${nextY + 20}, ${branchX} ${branchY}`;
                    circlePositions.push({ x: branchX, y: branchY, r: 2 + Math.random() * 2 });
                    pathData += ` M ${nextX} ${nextY}`;
                }
            }

            x = nextX;
            y = nextY;
        }

        return { pathData, circlePositions };
    }

    // ========================================
    // STATUS UPDATES
    // ========================================

    startStatusUpdates() {
        setInterval(() => {
            this.updateStatus();
        }, 50);
    }

    updateStatus() {
        if (!this.statusElements.state) return;

        if (this.isInfected) {
            const intensityPercent = Math.round(this.currentIntensity * 100);
            const durationMs = Math.round(this.currentDuration);

            if (this.statusElements.state) {
                this.statusElements.state.textContent = `INFECTED ${intensityPercent}%`;
                // Color shift: yellow (low) to red (high)
                const hue = 60 - (this.currentIntensity * 60);
                this.statusElements.state.style.color = `hsl(${hue}, 100%, 50%)`;
            }

            if (this.statusElements.duration) {
                this.statusElements.duration.textContent = `${durationMs}ms`;
            }
        } else {
            if (this.statusElements.state) {
                this.statusElements.state.textContent = 'HOST';
                this.statusElements.state.style.color = '#00ff41';
            }

            if (this.statusElements.duration) {
                this.statusElements.duration.textContent = '--';
            }
        }

        if (this.statusElements.next) {
            if (this.autoGlitch && this.nextGlitchTime > 0) {
                const remaining = Math.max(0, (this.nextGlitchTime - Date.now()) / 1000);
                this.statusElements.next.textContent = remaining.toFixed(1) + 's';
            } else {
                this.statusElements.next.textContent = 'off';
            }
        }

        if (this.statusElements.lambda) {
            this.statusElements.lambda.textContent = this.lambda.toFixed(2);
        }
    }

    // ========================================
    // CONTROLS SETUP
    // ========================================

    setupControls() {
        // Infection Level slider (unified control)
        const infectionLevelSlider = document.getElementById('infectionLevelSlider');
        const infectionLevelValue = document.getElementById('infectionLevelValue');
        const infectionLevelBar = document.getElementById('infectionLevelBar');
        const infectionLevelText = document.getElementById('infectionLevelText');

        if (infectionLevelSlider) {
            // Calculate initial level from current lambda (reverse mapping)
            const initialLevel = Math.round((this.lambda - 0.05) / 0.95 * 100);
            infectionLevelSlider.value = initialLevel;
            if (infectionLevelValue) infectionLevelValue.textContent = `${initialLevel}%`;
            if (infectionLevelBar) infectionLevelBar.style.width = `${initialLevel}%`;
            if (infectionLevelText) infectionLevelText.textContent = `${initialLevel}% TENDRL`;

            infectionLevelSlider.addEventListener('input', (e) => {
                const level = parseInt(e.target.value);
                this.applyInfectionLevel(level);

                // Update UI
                if (infectionLevelValue) infectionLevelValue.textContent = `${level}%`;
                if (infectionLevelBar) infectionLevelBar.style.width = `${level}%`;
                if (infectionLevelText) infectionLevelText.textContent = `${level}% TENDRL`;

                // Update advanced controls to reflect mapped values
                const lambdaSlider = document.getElementById('lambdaSlider');
                const lambdaValue = document.getElementById('lambdaValue');
                if (lambdaSlider) lambdaSlider.value = this.lambda;
                if (lambdaValue) lambdaValue.textContent = this.lambda.toFixed(2);

                const intensityAlphaSlider = document.getElementById('intensityAlphaSlider');
                const intensityAlphaValue = document.getElementById('intensityAlphaValue');
                if (intensityAlphaSlider) intensityAlphaSlider.value = this.intensityAlpha;
                if (intensityAlphaValue) intensityAlphaValue.textContent = this.intensityAlpha.toFixed(1);

                const intensityBetaSlider = document.getElementById('intensityBetaSlider');
                const intensityBetaValue = document.getElementById('intensityBetaValue');
                if (intensityBetaSlider) intensityBetaSlider.value = this.intensityBeta;
                if (intensityBetaValue) intensityBetaValue.textContent = this.intensityBeta.toFixed(1);

                const durationMeanSlider = document.getElementById('durationMeanSlider');
                const durationMeanValue = document.getElementById('durationMeanValue');
                if (durationMeanSlider) durationMeanSlider.value = this.durationMean;
                if (durationMeanValue) durationMeanValue.textContent = this.durationMean;

                const durationShapeSlider = document.getElementById('durationShapeSlider');
                const durationShapeValue = document.getElementById('durationShapeValue');
                if (durationShapeSlider) durationShapeSlider.value = this.durationShape;
                if (durationShapeValue) durationShapeValue.textContent = this.durationShape.toFixed(1);

                // Re-render distribution graphs
                this.renderDistributionGraphs();
            });
        }

        // Lambda slider
        const lambdaSlider = document.getElementById('lambdaSlider');
        const lambdaValue = document.getElementById('lambdaValue');
        if (lambdaSlider) {
            lambdaSlider.value = this.lambda;
            if (lambdaValue) lambdaValue.textContent = this.lambda.toFixed(2);

            lambdaSlider.addEventListener('input', (e) => {
                this.lambda = parseFloat(e.target.value);
                if (lambdaValue) lambdaValue.textContent = this.lambda.toFixed(2);
                this.saveSettings();

                // Reschedule next glitch with new lambda
                if (this.autoGlitch && !this.isInfected) {
                    clearTimeout(this.glitchTimeout);
                    this.scheduleNextGlitch();
                }

                // Update infection level display (reverse mapping)
                if (infectionLevelSlider) {
                    const level = Math.round((this.lambda - 0.05) / 0.95 * 100);
                    infectionLevelSlider.value = level;
                    if (infectionLevelValue) infectionLevelValue.textContent = `${level}%`;
                    if (infectionLevelBar) infectionLevelBar.style.width = `${level}%`;
                    if (infectionLevelText) infectionLevelText.textContent = `${level}% TENDRL`;
                }
            });
        }

        // Distribution select
        const distSelect = document.getElementById('distSelect');
        if (distSelect) {
            distSelect.value = this.distribution;
            distSelect.addEventListener('change', (e) => {
                this.distribution = e.target.value;
                this.saveSettings();

                // Reschedule next glitch with new distribution
                if (this.autoGlitch && !this.isInfected) {
                    clearTimeout(this.glitchTimeout);
                    this.scheduleNextGlitch();
                }
            });
        }

        // Intensity alpha slider
        const intensityAlphaSlider = document.getElementById('intensityAlphaSlider');
        const intensityAlphaValue = document.getElementById('intensityAlphaValue');
        if (intensityAlphaSlider) {
            intensityAlphaSlider.value = this.intensityAlpha;
            if (intensityAlphaValue) intensityAlphaValue.textContent = this.intensityAlpha.toFixed(1);

            intensityAlphaSlider.addEventListener('input', (e) => {
                this.intensityAlpha = parseFloat(e.target.value);
                if (intensityAlphaValue) intensityAlphaValue.textContent = this.intensityAlpha.toFixed(1);
                this.saveSettings();
                this.rescheduleIfNeeded();
            });
        }

        // Intensity beta slider
        const intensityBetaSlider = document.getElementById('intensityBetaSlider');
        const intensityBetaValue = document.getElementById('intensityBetaValue');
        if (intensityBetaSlider) {
            intensityBetaSlider.value = this.intensityBeta;
            if (intensityBetaValue) intensityBetaValue.textContent = this.intensityBeta.toFixed(1);

            intensityBetaSlider.addEventListener('input', (e) => {
                this.intensityBeta = parseFloat(e.target.value);
                if (intensityBetaValue) intensityBetaValue.textContent = this.intensityBeta.toFixed(1);
                this.saveSettings();
                this.rescheduleIfNeeded();
            });
        }

        // Duration mean slider
        const durationMeanSlider = document.getElementById('durationMeanSlider');
        const durationMeanValue = document.getElementById('durationMeanValue');
        if (durationMeanSlider) {
            durationMeanSlider.value = this.durationMean;
            if (durationMeanValue) durationMeanValue.textContent = this.durationMean;

            durationMeanSlider.addEventListener('input', (e) => {
                this.durationMean = parseInt(e.target.value);
                if (durationMeanValue) durationMeanValue.textContent = this.durationMean;
                this.saveSettings();
                this.rescheduleIfNeeded();
            });
        }

        // Duration shape slider
        const durationShapeSlider = document.getElementById('durationShapeSlider');
        const durationShapeValue = document.getElementById('durationShapeValue');
        if (durationShapeSlider) {
            durationShapeSlider.value = this.durationShape;
            if (durationShapeValue) durationShapeValue.textContent = this.durationShape.toFixed(1);

            durationShapeSlider.addEventListener('input', (e) => {
                this.durationShape = parseFloat(e.target.value);
                if (durationShapeValue) durationShapeValue.textContent = this.durationShape.toFixed(1);
                this.saveSettings();
                this.rescheduleIfNeeded();
            });
        }

        // Auto glitch toggle
        const autoGlitchCheckbox = document.getElementById('autoGlitch');
        if (autoGlitchCheckbox) {
            autoGlitchCheckbox.checked = this.autoGlitch;
            autoGlitchCheckbox.addEventListener('change', (e) => {
                this.autoGlitch = e.target.checked;
                this.saveSettings();

                if (this.autoGlitch) {
                    this.scheduleNextGlitch();
                } else {
                    clearTimeout(this.glitchTimeout);
                }
            });
        }

        // Manual trigger button
        const manualGlitch = document.getElementById('manualGlitch');
        if (manualGlitch) {
            manualGlitch.addEventListener('click', () => {
                this.triggerInfection();
            });
        }

        // Full takeover button
        const manualFullGlitch = document.getElementById('manualFullGlitch');
        if (manualFullGlitch) {
            manualFullGlitch.addEventListener('click', () => {
                this.fullTakeover();
            });
        }

        // Theme toggle (host dark mode)
        const themeToggle = document.getElementById('themeToggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => {
                this.body.classList.toggle('host-dark');
                this.triggerInfection();
            });
        }

        // Logo glitch speed slider
        const logoGlitchSpeedSlider = document.getElementById('logoGlitchSpeed');
        const logoGlitchSpeedValue = document.getElementById('logoGlitchSpeedValue');
        if (logoGlitchSpeedSlider) {
            logoGlitchSpeedSlider.value = this.logoGlitchSpeed;
            if (logoGlitchSpeedValue) logoGlitchSpeedValue.textContent = `${this.logoGlitchSpeed} Hz`;

            logoGlitchSpeedSlider.addEventListener('input', (e) => {
                this.logoGlitchSpeed = parseFloat(e.target.value);
                if (logoGlitchSpeedValue) logoGlitchSpeedValue.textContent = `${this.logoGlitchSpeed} Hz`;
                this.saveSettings();
                this.updateLogoAnimationSpeed();
            });

            // Set initial speed
            this.updateLogoAnimationSpeed();
        }

        // Initialize distribution visualizations
        this.renderDistributionGraphs();

        // Update graphs when sliders change
        if (lambdaSlider) lambdaSlider.addEventListener('input', () => this.renderDistributionGraphs());
        if (distSelect) distSelect.addEventListener('change', () => this.renderDistributionGraphs());
        if (intensityAlphaSlider) intensityAlphaSlider.addEventListener('input', () => this.renderDistributionGraphs());
        if (intensityBetaSlider) intensityBetaSlider.addEventListener('input', () => this.renderDistributionGraphs());
        if (durationMeanSlider) durationMeanSlider.addEventListener('input', () => this.renderDistributionGraphs());
        if (durationShapeSlider) durationShapeSlider.addEventListener('input', () => this.renderDistributionGraphs());
    }

    renderDistributionGraphs() {
        this.renderIntervalDistribution();
        this.renderIntensityDistribution();
        this.renderDurationDistribution();
    }

    renderIntervalDistribution() {
        const svg = document.getElementById('intervalDistGraph');
        if (!svg) return;

        const width = 200;
        const height = 40;
        const samples = 100;
        const points = [];

        // Sample the distribution
        for (let i = 0; i < samples; i++) {
            const x = (i / samples) * 10; // 0 to 10 seconds
            let y;

            switch (this.distribution) {
                case 'exponential':
                    y = this.lambda * Math.exp(-this.lambda * x);
                    break;
                case 'poisson':
                    // Approximation: sum of exponentials
                    y = this.lambda * Math.exp(-this.lambda * x) * 0.8;
                    break;
                case 'uniform':
                    y = (x >= 1 && x <= 9) ? 0.125 : 0;
                    break;
                default:
                    y = this.lambda * Math.exp(-this.lambda * x);
            }

            points.push({ x: (i / samples) * width, y: height - (y * height * 3) });
        }

        // Create path
        const pathData = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${Math.max(2, Math.min(height - 2, p.y))}`).join(' ');
        const fillData = `M 0,${height} ${pathData} L ${width},${height} Z`;

        svg.innerHTML = `
            <path class="dist-fill" d="${fillData}"/>
            <path d="${pathData}"/>
        `;
    }

    renderIntensityDistribution() {
        const svg = document.getElementById('intensityDistGraph');
        if (!svg) return;

        const width = 200;
        const height = 40;
        const samples = 100;
        const points = [];

        // Beta distribution PDF approximation
        for (let i = 0; i < samples; i++) {
            const x = i / samples; // 0 to 1
            const a = this.intensityAlpha;
            const b = this.intensityBeta;

            // Beta PDF: x^(a-1) * (1-x)^(b-1) / B(a,b)
            // Simplified for visualization (not normalized)
            const y = Math.pow(x + 0.001, a - 1) * Math.pow(1 - x + 0.001, b - 1);

            points.push({ x: (i / samples) * width, y: height - Math.min(y * height * 0.3, height - 4) });
        }

        const pathData = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${Math.max(2, p.y)}`).join(' ');
        const fillData = `M 0,${height} ${pathData} L ${width},${height} Z`;

        svg.innerHTML = `
            <path class="dist-fill" d="${fillData}"/>
            <path d="${pathData}"/>
        `;
    }

    renderDurationDistribution() {
        const svg = document.getElementById('durationDistGraph');
        if (!svg) return;

        const width = 200;
        const height = 40;
        const samples = 100;
        const points = [];

        const k = this.durationShape;
        const theta = this.durationMean / k;

        // Gamma distribution PDF
        for (let i = 1; i < samples; i++) {
            const x = (i / samples) * (this.durationMean * 3); // 0 to 3*mean

            // Gamma PDF: (x^(k-1) * exp(-x/theta)) / (theta^k * Gamma(k))
            // Simplified for visualization
            const y = Math.pow(x, k - 1) * Math.exp(-x / theta) / Math.pow(theta, k);

            points.push({ x: (i / samples) * width, y: height - Math.min(y * height * theta * 10, height - 4) });
        }

        const pathData = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${Math.max(2, p.y)}`).join(' ');
        const fillData = `M 0,${height} ${pathData} L ${width},${height} Z`;

        svg.innerHTML = `
            <path class="dist-fill" d="${fillData}"/>
            <path d="${pathData}"/>
        `;
    }

    setupCardHover() {
        // Add infection chance on card hover
        document.querySelectorAll('.note, .publication-card').forEach(card => {
            card.addEventListener('mouseenter', () => {
                if (Math.random() < this.hoverInfectionChance) {
                    this.triggerInfection();
                }
            });
        });
    }

    // ========================================
    // PUBLIC API
    // ========================================

    /**
     * Enable/disable auto-glitch
     */
    setAutoGlitch(enabled) {
        this.autoGlitch = enabled;
        if (enabled) {
            this.scheduleNextGlitch();
        } else {
            clearTimeout(this.glitchTimeout);
        }
    }

    /**
     * Update lambda (glitch frequency)
     */
    setLambda(lambda) {
        this.lambda = Math.max(0.05, Math.min(1.0, lambda));
    }

    /**
     * Destroy the theme engine
     */
    destroy() {
        clearTimeout(this.glitchTimeout);
        clearTimeout(this.recoveryTimeout);
        this.body.classList.remove('parasitic-theme', 'infected', 'host-dark');
        this.body.style.removeProperty('--infection-intensity');

        // Remove overlays
        document.querySelectorAll('.infection-overlay, .glitch-flash, .screen-tear, .vine-container').forEach(el => {
            el.remove();
        });
    }
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ParasiticTheme };
}

// Auto-initialize if data attribute present
document.addEventListener('DOMContentLoaded', () => {
    const autoInit = document.querySelector('[data-parasitic-theme]');
    if (autoInit) {
        const config = {};
        const dataset = autoInit.dataset;

        if (dataset.lambda) config.lambda = parseFloat(dataset.lambda);
        if (dataset.autoGlitch !== undefined) config.autoGlitch = dataset.autoGlitch !== 'false';
        if (dataset.intensityAlpha) config.intensityAlpha = parseFloat(dataset.intensityAlpha);
        if (dataset.intensityBeta) config.intensityBeta = parseFloat(dataset.intensityBeta);
        if (dataset.durationMean) config.durationMean = parseInt(dataset.durationMean);
        if (dataset.durationShape) config.durationShape = parseFloat(dataset.durationShape);

        window.parasiticTheme = new ParasiticTheme(config);
    }

    // Settings panel toggle
    const settingsToggle = document.getElementById('infection-settings-toggle');
    const settingsPanel = document.getElementById('infection-settings');

    if (settingsToggle && settingsPanel) {
        settingsToggle.addEventListener('click', () => {
            settingsPanel.classList.toggle('hidden');
            // Trigger a small infection on toggle
            if (window.parasiticTheme && !settingsPanel.classList.contains('hidden')) {
                window.parasiticTheme.triggerInfection(0.3, 200);
            }
        });

        // Close panel when clicking outside
        document.addEventListener('click', (e) => {
            if (!settingsPanel.contains(e.target) &&
                !settingsToggle.contains(e.target) &&
                !settingsPanel.classList.contains('hidden')) {
                settingsPanel.classList.add('hidden');
            }
        });
    }

    // Theme toggle (dark mode) with persistence
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        // Restore saved theme preference
        const savedTheme = localStorage.getItem('tendrl-theme');
        if (savedTheme === 'dark') {
            document.body.classList.add('host-dark');
        }

        themeToggle.addEventListener('click', () => {
            document.body.classList.toggle('host-dark');

            // Save preference
            const isDark = document.body.classList.contains('host-dark');
            localStorage.setItem('tendrl-theme', isDark ? 'dark' : 'light');

            if (window.parasiticTheme) {
                window.parasiticTheme.triggerInfection();
            }
        });
    }
});
