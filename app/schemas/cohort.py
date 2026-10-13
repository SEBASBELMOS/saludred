"""Pydantic models for the triage cohort endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClusterRead(BaseModel):
    cluster: int = Field(description="Numero de grupo; 1 es el mas lejano al caso critico")
    zona: str = Field(description="lejos, intermedio o cerca del caso critico")
    descripcion: str = Field(description="Lectura clinica del grupo")
    n_reales: int = Field(description="Filas reales (MIMIC-IV-ED Demo)")
    n_sinteticos: int = Field(description="Filas sinteticas generadas dentro del grupo")
    tasa_ingreso: float = Field(description="Proporcion de estancias reales que terminaron en ingreso")
    silueta: float | None = Field(description="Silueta media del grupo (-1 a 1)")
