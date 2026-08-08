import { BoxGeometry, MeshStandardMaterial } from 'three'
import { describe, expect, it, vi } from 'vitest'

import { ResourcePool } from '@/viewer/resources'

describe('pool de ressources', () => {
  it('ne construit qu’une fois par clé', () => {
    const pool = new ResourcePool()
    const create = vi.fn(() => new BoxGeometry(1, 1, 1))

    const first = pool.acquire('boite', create)
    const second = pool.acquire('boite', create)

    expect(create).toHaveBeenCalledTimes(1)
    expect(second).toBe(first)
    expect(pool.reuseCount).toBe(1)
    expect(pool.size).toBe(1)
  })

  it('distingue deux clés', () => {
    const pool = new ResourcePool()
    pool.acquire('a', () => new BoxGeometry(1, 1, 1))
    pool.acquire('b', () => new BoxGeometry(2, 2, 2))
    expect(pool.size).toBe(2)
  })

  it('libère aussi les ressources sans clé', () => {
    // Une géométrie de mur est unique par construction : elle n'a pas de clé, mais elle doit
    // être rendue comme les autres. C'est exactement ce qui fuyait quand elle naissait dans un
    // `computed`.
    const pool = new ResourcePool()
    const geometry = pool.own(new BoxGeometry(1, 1, 1))
    const rendered = vi.spyOn(geometry, 'dispose')

    pool.dispose()

    expect(rendered).toHaveBeenCalledTimes(1)
    expect(pool.size).toBe(0)
  })

  it('libère tout, matériaux compris', () => {
    const pool = new ResourcePool()
    const material = pool.acquire('mat', () => new MeshStandardMaterial())
    const geometry = pool.own(new BoxGeometry(1, 1, 1))
    const materialDisposed = vi.spyOn(material, 'dispose')
    const geometryDisposed = vi.spyOn(geometry, 'dispose')

    pool.dispose()

    expect(materialDisposed).toHaveBeenCalledTimes(1)
    expect(geometryDisposed).toHaveBeenCalledTimes(1)
  })

  it('ne libère pas deux fois', () => {
    // Un composant démonté pendant qu'un watch est en vol appellerait `dispose()` deux fois.
    const pool = new ResourcePool()
    const geometry = pool.own(new BoxGeometry(1, 1, 1))
    const rendered = vi.spyOn(geometry, 'dispose')

    pool.dispose()
    pool.dispose()

    expect(rendered).toHaveBeenCalledTimes(1)
  })
})
