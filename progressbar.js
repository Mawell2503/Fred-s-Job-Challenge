(function (global) {
        'use strict';

        /* ---------- 1. TUNABLE LOOK (px at a 680px-wide bar) ---------- */
        const CONFIG= {
            dissolve: 70, // width of the grainy zone behind the edge
            ragged: 30, // how far the edge wobbles (px of horizontal travel)
            grain: 1.5, // size of one speckle
            cell: 14, // size of one green dome
            ease: 7, // how quickly the shown value chases the target (higher = snappier)
            fps: 10, // how often the speckle re-rolls (the shimmer)
        }

        ;

        /* ---------- 2. THE SHADER ---------- */
        const VERT=` attribute vec2 aPos;

        void main() {
            gl_Position=vec4(aPos, 0.0, 1.0);
        }

        `;

        const FRAG=` precision highp float;

        uniform vec2 uRes; // bar size in CSS px
        uniform float uDpr; // device pixel ratio
        uniform float uScale; // bar width / 680 -> keeps details proportional
        uniform float uProgress; // 0..1 (already smoothed in JS)
        uniform float uTime; // seconds
        uniform float uDissolve, uRagged, uGrain, uCell, uFps;

        const vec3 BG=vec3(0.075, 0.071, 0.067); // the dark pill colour

        /* A. cheap pseudo-random number from a 2D coordinate (0..1) */
        float hash12(vec2 p) {
            vec3 p3=fract(vec3(p.xyx) * .1031);
            p3 +=dot(p3, p3.yzx + 33.33);
            return fract((p3.x + p3.y) * p3.z);
        }

        /* B. smooth value noise: random values on a grid, blended */
        float vnoise(vec2 p) {
            vec2 i=floor(p), f=fract(p);
            f=f*f*(3.0 - 2.0*f);
            return mix(mix(hash12(i), hash12(i+vec2(1., 0.)), f.x),
                mix(hash12(i+vec2(0., 1.)), hash12(i+vec2(1., 1.)), f.x), f.y);
        }

        /* C. fbm = 3 layers of noise at doubling frequency (organic wobble) */
        float fbm(vec2 p) {
            float a=.5, s=0.;

            for(int i=0; i < 3; i++) {
                s +=a*vnoise(p); p *=2.03; a *=.5;
            }

            return s / .875;
        }

        /* D. the solid fill: a brick-offset lattice of lit green domes */
        vec3 domes(vec2 p) {
            vec2 q=p / (uCell * uScale);
            q.x +=.5 * mod(floor(q.y), 2.); // shift every other row
            vec2 id=floor(q); // which dome am I in?
            vec2 f=fract(q) - .5; // position inside it
            float h=hash12(id); // per-dome random number

            float rad=.44 + .05*sin(uTime*1.3 + h*6.2831); // breathing radius
            vec2 u=f / rad;
            float r2=dot(u, u);
            float z=sqrt(max(0., 1. - r2)); // hemisphere height
            vec3 n=normalize(vec3(u, z + .001)); // surface normal

            vec3 L=normalize(vec3(-.45, -.65, .62)); // light from top-left
            float dif=clamp(dot(n, L), 0., 1.);
            float spc=pow(clamp(dot(n, normalize(L + vec3(0., 0., 1.))), 0., 1.), 40.);

            vec3 dark=vec3(.05, .17, .05);
            vec3 base=mix(vec3(.20, .55, .12), vec3(.50, .82, .20), h); // each dome a slightly different green
            vec3 c=mix(dark, base, dif) * (.55 + .6*z) + spc*.55;

            float inside=1. - smoothstep(.88, 1., sqrt(r2)); // soft dome silhouette
            return mix(vec3(.03, .09, .03), c, inside); // dark gap between domes
        }

        void main() {
            /* pixel position in CSS px, origin top-left (GL's y points up, so flip) */
            vec2 px=vec2(gl_FragCoord.x, uRes.y*uDpr - gl_FragCoord.y) / uDpr;

            float dissolve=uDissolve * uScale;
            float ragged=uRagged * uScale;
            float p=uProgress;

            /* 1. WHERE IS THE EDGE?
     At p=0 the edge is at x=0 (nothing visible). At p=1 it has travelled the
     full width PLUS the dissolve zone PLUS the wobble, so even the grainy tail
     is off the right side -> 100% solid. */
            float travel=uRes.x + dissolve + ragged;
            float edge=p * travel;

            /* wobble: noise along y (bumpy top-to-bottom) and time (crawls); faded out
     near 0% and 100% so those states are clean */
            float env=smoothstep(0., .05, p) * (1. - smoothstep(.95, 1., p));
            edge +=(fbm(vec2(px.y*.035/uScale, uTime*.5)) - .5) * ragged * env;

            /* 2. HOW FAR BEHIND THE EDGE AM I? */
            float d=edge - px.x; // <0 ahead, >0 behind
            float dens=smoothstep(0., 1., clamp(d / dissolve, 0., 1.));

            /* 3. DITHER: each speckle-cell rolls a die; "on" if the die is below the
     density. Re-rolled a few times a second (shimmer). */
            vec2 gc=floor(px / (uGrain * uScale));
            float rnd=hash12(gc + floor(uTime*uFps)*17.);
            float on=float(rnd < dens);

            /* 4. COLOUR: speckles use the dome pattern, dimmed where sparse */
            vec3 fill=domes(px) * mix(.28, 1., dens * dens);
            gl_FragColor=vec4(mix(BG, fill, on), 1.);
        }

        `;

        /* ---------- 3. THE COMPONENT CLASS ---------- */
        class ShaderProgress {
            constructor(root, opts) {
                this.root=root;
                this.canvas=root.querySelector('canvas');
                this.pctEl=root.querySelector('.progress__pct');

                this.cfg=Object.assign({}

                , CONFIG, opts || {});

            this.target=0; // value you asked for (0..1)
            this.shown=0; // value currently drawn (chases target)
            this.lastPct=-1;
            this.t0=performance.now();
            this.last=this.t0;
            this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;

            if ( !this._initGL()) {
                this._fallback(); return;
            }

            this.ro=new ResizeObserver(()=> this._resize());
            this.ro.observe(root);
            this._resize();
            requestAnimationFrame(t=> this._frame(t));
        }

        /* ---- public API ---- */
        setProgress(v) {
            this.target=Math.min(1, Math.max(0, v));
        }

        // v in 0..1
        jump(v) {
            this.setProgress(v); this.shown=this.target;
        }

        // no easing

        /* ---- WebGL setup ---- */
        _initGL() {
            const gl=this.gl=this.canvas.getContext('webgl', {
                antialias: false, alpha: false
            });
        if ( !gl) return false;

        const sh=(type, src)=> {
            const s=gl.createShader(type);
            gl.shaderSource(s, src); gl.compileShader(s);

            if ( !gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
                console.error(gl.getShaderInfoLog(s)); return null;
            }

            return s;
        }

        ;
        const vs=sh(gl.VERTEX_SHADER, VERT), fs=sh(gl.FRAGMENT_SHADER, FRAG);
        if ( !vs || !fs) return false;

        const prog=this.prog=gl.createProgram();
        gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
        if ( !gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
        gl.useProgram(prog);

        /* one big triangle covering the canvas; the fragment shader then runs
               once per pixel */
        const buf=gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        const loc=gl.getAttribLocation(prog, 'aPos');
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        this.u= {}

        ;
        ['uRes', 'uDpr', 'uScale', 'uProgress', 'uTime', 'uDissolve', 'uRagged', 'uGrain', 'uCell', 'uFps'] .forEach(n=> this.u[n]=gl.getUniformLocation(prog, n));
        return true;
    }

    /* WebGL unavailable: a plain green CSS bar so nothing breaks */
    _fallback() {
        const f=document.createElement('div');
        f.style.cssText='position:absolute;inset:0 auto 0 0;background:#3f9a1a;width:0;z-index:0';
        this.canvas.replaceWith (f);

        const tick=()=> {
            this.shown +=(this.target - this.shown) * 0.12;
            f.style.width=this.shown * 100 + '%';
            this._text();
            requestAnimationFrame(tick);
        }

        ;
        tick();
    }

    /* keep the canvas pixel buffer matched to its on-screen size x DPR */
    _resize() {
        const dpr=Math.min(window.devicePixelRatio || 1, 2);
        const r=this.root.getBoundingClientRect();
        this.w=r.width; this.h=r.height; this.dpr=dpr;
        this.canvas.width=Math.round(r.width * dpr);
        this.canvas.height=Math.round(r.height * dpr);
        this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    }

    /* update the % number + aria */
    _text() {
        const pct=Math.round(this.shown * 100);

        if (pct !==this.lastPct) {
            this.lastPct=pct;
            this.pctEl.textContent=pct;
            this.root.setAttribute('aria-valuenow', pct);
        }
    }

    /* ---- one animation frame ---- */
    _frame(now) {
        const dt=Math.min(0.05, (now - this.last) / 1000);
        this.last=now;

        /* exponential smoothing: frame-rate independent "chase the target" */
        this.shown +=(this.target - this.shown) * (1 - Math.exp(-this.cfg.ease * dt));
        if (Math.abs(this.target - this.shown) < 1e-4) this.shown=this.target;
        this._text();

        const gl=this.gl, u=this.u, c=this.cfg;
        const time=this.reduced ? 0 : (now - this.t0) / 1000;

        gl.uniform2f(u.uRes, this.w, this.h);
        gl.uniform1f(u.uDpr, this.dpr);
        gl.uniform1f(u.uScale, this.w / 680);
        gl.uniform1f(u.uProgress, this.shown);
        gl.uniform1f(u.uTime, time);
        gl.uniform1f(u.uDissolve, c.dissolve);
        gl.uniform1f(u.uRagged, c.ragged);
        gl.uniform1f(u.uGrain, c.grain);
        gl.uniform1f(u.uCell, c.cell);
        gl.uniform1f(u.uFps, c.fps);
        gl.drawArrays(gl.TRIANGLES, 0, 3);

        requestAnimationFrame(t=> this._frame(t));
    }
}

global.ShaderProgress=ShaderProgress;
})(window);