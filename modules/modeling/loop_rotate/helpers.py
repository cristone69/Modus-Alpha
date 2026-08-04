# SPDX-License-Identifier: GPL-3.0-or-later
"""Topology and vector helpers for Loop Rotate."""

from mathutils import Vector


_EPSILON = 1.0e-10


def ordered_edge_chains(edges):
    """Return selected edges as ordered, non-branching vertex chains."""
    unused = set(edges)
    chains = []

    while unused:
        seed = next(iter(unused))
        component_edges = {seed}
        component_verts = set(seed.verts)
        pending = [seed]

        while pending:
            edge = pending.pop()
            for vert in edge.verts:
                for linked in vert.link_edges:
                    if linked in unused and linked not in component_edges:
                        component_edges.add(linked)
                        component_verts.update(linked.verts)
                        pending.append(linked)

        neighbours = {vert: [] for vert in component_verts}
        for edge in component_edges:
            first, second = edge.verts
            neighbours[first].append((edge, second))
            neighbours[second].append((edge, first))

        if any(len(linked) > 2 for linked in neighbours.values()):
            raise ValueError("Loop Rotate requires unbranched edge selections")

        endpoints = [vert for vert, linked in neighbours.items() if len(linked) == 1]
        cyclic = not endpoints
        start = min(endpoints or component_verts, key=lambda vert: vert.index)

        vertices = [start]
        ordered_edges = []
        visited_edges = set()
        current = start

        while True:
            choices = [
                (edge, other)
                for edge, other in neighbours[current]
                if edge not in visited_edges
            ]
            if not choices:
                break

            edge, other = min(choices, key=lambda item: item[0].index)
            visited_edges.add(edge)
            ordered_edges.append(edge)

            if cyclic and other is start:
                break

            vertices.append(other)
            current = other

        unused.difference_update(component_edges)
        if len(vertices) >= 2:
            chains.append({
                "verts": vertices,
                "edges": ordered_edges,
                "cyclic": cyclic,
            })

    return chains


def mean_position(positions):
    """Return the arithmetic mean of one or more vectors."""
    total = Vector((0.0, 0.0, 0.0))
    for position in positions:
        total += position
    return total / len(positions)


def tangent_at(vertices, index, cyclic, positions):
    """Calculate the centered tangent of an ordered vertex chain."""
    count = len(vertices)
    current = positions[vertices[index]]

    if cyclic or 0 < index < count - 1:
        previous = positions[vertices[(index - 1) % count]]
        following = positions[vertices[(index + 1) % count]]
        incoming = current - previous
        outgoing = following - current

        if incoming.length_squared > _EPSILON:
            incoming.normalize()
        if outgoing.length_squared > _EPSILON:
            outgoing.normalize()

        tangent = incoming + outgoing
        if tangent.length_squared <= _EPSILON:
            tangent = following - previous
    elif index == 0:
        tangent = positions[vertices[1]] - current
    else:
        tangent = current - positions[vertices[-2]]

    if tangent.length_squared > _EPSILON:
        tangent.normalize()
    return tangent


def unique_directions(directions, tolerance=0.9999):
    """Remove duplicate and antiparallel directions."""
    unique = []
    for direction in directions:
        if direction.length_squared <= _EPSILON:
            continue

        direction = direction.normalized()
        if not any(abs(direction.dot(existing)) >= tolerance for existing in unique):
            unique.append(direction)

    return unique
