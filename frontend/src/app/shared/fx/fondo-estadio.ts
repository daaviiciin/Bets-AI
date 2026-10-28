import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  inject,
} from '@angular/core';
import { gsap } from 'gsap';
import type { Material } from 'three';

import { movimientoReducido } from '../tv-motion';

/*
 * Estadio de noche en un solo plano a pantalla completa: niebla que deriva, dos focos que
 * barren desde las esquinas, las líneas del césped en perspectiva (avanzan con el tiempo) y
 * una viñeta. Todo oscuro y frío a propósito: debajo van tablas de cuotas que tienen que
 * leerse; el fondo no puede competir con ellas.
 */
const FRAG_ESTADIO = /* glsl */ `
  precision highp float;
  uniform float uTime;
  uniform vec2 uRes;
  uniform vec2 uMouse;
  varying vec2 vUv;

  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x),
               mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }
  float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + 11.7; a *= 0.5; }
    return v;
  }

  // Cono de luz desde o, apuntando a "ang" (radianes); se abre y se apaga con la distancia.
  float foco(vec2 p, vec2 o, float ang) {
    vec2 d = vec2(cos(ang), sin(ang));
    vec2 v = p - o;
    float a = dot(v, d);
    float c = abs(v.x * d.y - v.y * d.x);
    float ancho = 0.03 + a * 0.24;
    return smoothstep(ancho, 0.0, c) * smoothstep(0.0, 0.25, a) * exp(-a * 0.75);
  }

  void main() {
    vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
    float asp = uRes.x / uRes.y;
    float t = uTime;

    vec3 noche = vec3(0.043, 0.071, 0.133);
    vec3 col = noche;

    float n = fbm(p * 1.4 + vec2(t * 0.018, -t * 0.011) + uMouse * 0.08);
    float n2 = fbm(p * 3.1 - vec2(t * 0.03, 0.0));
    col += vec3(0.07, 0.11, 0.21) * n * 0.75 + vec3(0.03, 0.05, 0.1) * n2 * 0.4;

    // Focos: barren despacio, desfasados, y respiran con la niebla.
    float f1 = foco(p, vec2(-0.55 * asp, 0.62), -0.85 + sin(t * 0.13) * 0.28 + uMouse.x * 0.06);
    float f2 = foco(p, vec2(0.55 * asp, 0.62), -2.29 + sin(t * 0.11 + 2.1) * 0.28 + uMouse.x * 0.06);
    col += vec3(0.5, 0.62, 0.95) * (f1 + f2) * 0.13 * (0.55 + 0.6 * n);

    // Césped en perspectiva: la mitad inferior, líneas que avanzan con el tiempo.
    float h = -p.y + 0.06;
    if (h > 0.0) {
      float z = 0.32 / h;
      vec2 g = vec2(p.x * z + uMouse.x * 0.3, z + t * 0.12);
      vec2 gl = abs(fract(g * vec2(1.2, 0.6)) - 0.5) / fwidth(g * vec2(1.2, 0.6));
      float linea = 1.0 - min(min(gl.x, gl.y), 1.0);
      float fade = smoothstep(9.0, 1.2, z) * smoothstep(0.0, 0.08, h);
      col += vec3(0.16, 0.27, 0.5) * linea * fade * 0.22;
      // Bandas de siega, muy tenues.
      col += vec3(0.02, 0.04, 0.07) * step(0.5, fract(g.y * 0.3)) * fade;
    }

    // Viñeta y grano contra el bandeado del degradado.
    vec2 q = gl_FragCoord.xy / uRes;
    col *= 0.55 + 0.45 * pow(16.0 * q.x * q.y * (1.0 - q.x) * (1.0 - q.y), 0.22);
    col += (hash(gl_FragCoord.xy + t) - 0.5) / 255.0;

    gl_FragColor = vec4(col, 1.0);
  }
`;

const VERT_PLANO = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

/* Polvo en suspensión bajo los focos: sube despacio y titila. La posición se calcula en la
   GPU a partir del tiempo, así que la CPU no toca ninguna partícula por fotograma. */
const VERT_POLVO = /* glsl */ `
  attribute float aSemilla;
  uniform float uTime;
  uniform vec2 uMouse;
  uniform float uPx;
  varying float vBrillo;
  void main() {
    vec3 p = position;
    float v = 0.015 + aSemilla * 0.03;
    p.y = mod(p.y + 1.2 + uTime * v, 2.4) - 1.2;
    p.x += sin(uTime * 0.3 + aSemilla * 40.0) * 0.03 + uMouse.x * 0.04 * (0.5 + p.z);
    vBrillo = 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * (1.0 + aSemilla * 2.0) + aSemilla * 90.0));
    gl_Position = vec4(p.xy, 0.0, 1.0);
    gl_PointSize = (1.2 + p.z * 2.4) * uPx;
  }
`;

const FRAG_POLVO = /* glsl */ `
  precision highp float;
  varying float vBrillo;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.0, d) * vBrillo * 0.5;
    gl_FragColor = vec4(vec3(0.72, 0.8, 1.0) * a, a);
  }
`;

const N_POLVO = 260;

/**
 * Fondo animado de toda la app (Three.js sobre un canvas fijo detrás del contenido). Three se
 * carga a demanda para no engordar el arranque. Se para con la pestaña oculta; con "reducir
 * movimiento" pinta un solo fotograma quieto. Si no hay WebGL, se queda el azul noche del body.
 */
@Component({
  selector: 'app-fondo-estadio',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'pointer-events-none fixed inset-0 -z-10 block', 'aria-hidden': 'true' },
  template: `<canvas #lienzo class="block h-full w-full opacity-0"></canvas>`,
})
export class FondoEstadio {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      let parar: (() => void) | null = null;
      let destruido = false;
      this.montar().then((fn) => (destruido ? fn() : (parar = fn)), () => undefined);
      destroyRef.onDestroy(() => {
        destruido = true;
        parar?.();
      });
    });
  }

  private async montar(): Promise<() => void> {
    const THREE = await import('three');
    const canvas = this.host.nativeElement.querySelector('canvas')!;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'low-power' });
    const px = Math.min(devicePixelRatio, 1.25);
    renderer.setPixelRatio(px);

    const escena = new THREE.Scene();
    const camara = new THREE.Camera();
    const uniforms = {
      uTime: { value: 0 },
      uRes: { value: new THREE.Vector2() },
      uMouse: { value: new THREE.Vector2() },
      uPx: { value: px },
    };

    const plano = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2),
      new THREE.ShaderMaterial({ vertexShader: VERT_PLANO, fragmentShader: FRAG_ESTADIO, uniforms, depthTest: false }),
    );
    escena.add(plano);

    const pos = new Float32Array(N_POLVO * 3);
    const semilla = new Float32Array(N_POLVO);
    for (let i = 0; i < N_POLVO; i++) {
      pos[i * 3] = Math.random() * 2 - 1;
      pos[i * 3 + 1] = Math.random() * 2.4 - 1.2;
      pos[i * 3 + 2] = Math.random();
      semilla[i] = Math.random();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aSemilla', new THREE.BufferAttribute(semilla, 1));
    const polvo = new THREE.Points(
      geo,
      new THREE.ShaderMaterial({
        vertexShader: VERT_POLVO,
        fragmentShader: FRAG_POLVO,
        uniforms,
        transparent: true,
        depthTest: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    escena.add(polvo);

    const ajustar = () => {
      renderer.setSize(innerWidth, innerHeight, false);
      renderer.getDrawingBufferSize(uniforms.uRes.value);
    };
    ajustar();
    addEventListener('resize', ajustar);

    // El ratón inclina un poco la escena; quickTo suaviza el seguimiento sin crear tweens nuevos.
    const raton = { x: 0, y: 0 };
    const sigueX = gsap.quickTo(raton, 'x', { duration: 1.6, ease: 'power3.out' });
    const sigueY = gsap.quickTo(raton, 'y', { duration: 1.6, ease: 'power3.out' });
    const mover = (e: PointerEvent) => {
      sigueX(e.clientX / innerWidth - 0.5);
      sigueY(0.5 - e.clientY / innerHeight);
    };
    addEventListener('pointermove', mover, { passive: true });

    const pintar = (t: number) => {
      uniforms.uTime.value = t;
      uniforms.uMouse.value.set(raton.x, raton.y);
      renderer.render(escena, camara);
    };

    const tick = (t: number) => {
      if (!document.hidden) pintar(t);
    };
    if (movimientoReducido()) pintar(12);
    else gsap.ticker.add(tick);
    gsap.to(canvas, { opacity: 1, duration: 1.2, ease: 'power2.out' });

    return () => {
      gsap.ticker.remove(tick);
      removeEventListener('resize', ajustar);
      removeEventListener('pointermove', mover);
      geo.dispose();
      plano.geometry.dispose();
      (plano.material as Material).dispose();
      (polvo.material as Material).dispose();
      renderer.dispose();
    };
  }
}
