import { useEffect, useRef } from "react"

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  alpha: number
  color: string
  radius: number
  decay: number
}

const COLORS = [
  "#22c55e",
  "#3b82f6",
  "#f59e0b",
  "#ef4444",
  "#a855f7",
  "#06b6d4",
]

function randomBetween(lower: number, upper: number) {
  return lower + Math.random() * (upper - lower)
}

function createBurst(cx: number, cy: number): Particle[] {
  const count = 30 + Math.floor(Math.random() * 20)
  const color = COLORS[Math.floor(Math.random() * COLORS.length)]
  const particles: Particle[] = []
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2
    const speed = randomBetween(1, 5)
    particles.push({
      x: cx,
      y: cy,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      alpha: 1,
      color,
      radius: randomBetween(1.5, 3),
      decay: randomBetween(0.015, 0.03),
    })
  }
  return particles
}

export function Fireworks({
  width = 400,
  height = 200,
}: {
  width?: number
  height?: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    const particles: Particle[] = []
    let animationFrameId: number
    let burstIndex = 0
    const burstTimers: ReturnType<typeof setTimeout>[] = []

    const positions = [
      [width * 0.25, height * 0.35],
      [width * 0.5, height * 0.25],
      [width * 0.75, height * 0.4],
    ]

    positions.forEach(([x, y], posIndex) => {
      const timer = setTimeout(() => {
        particles.push(...createBurst(x, y))
        burstIndex++
      }, posIndex * 400)
      burstTimers.push(timer)
    })

    function animate() {
      ctx!.clearRect(0, 0, width, height)

      for (let i = particles.length - 1; i >= 0; i--) {
        const particle = particles[i]
        particle.x += particle.vx
        particle.y += particle.vy
        particle.vy += 0.06
        particle.vx *= 0.98
        particle.alpha -= particle.decay

        if (particle.alpha <= 0) {
          particles.splice(i, 1)
          continue
        }

        ctx!.globalAlpha = particle.alpha
        ctx!.fillStyle = particle.color
        ctx!.beginPath()
        ctx!.arc(
          particle.x,
          particle.y,
          particle.radius,
          0,
          Math.PI * 2,
        )
        ctx!.fill()
      }

      ctx!.globalAlpha = 1

      if (particles.length > 0 || burstIndex < positions.length) {
        animationFrameId = requestAnimationFrame(animate)
      }
    }

    animationFrameId = requestAnimationFrame(animate)

    return () => {
      cancelAnimationFrame(animationFrameId)
      burstTimers.forEach(clearTimeout)
    }
  }, [width, height])

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
      className="pointer-events-none"
    />
  )
}
