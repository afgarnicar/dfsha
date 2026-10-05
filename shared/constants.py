"""Constantes compartidas del sistema DFSha.

Estos valores derivan de los parámetros fijos del proyecto. Los parámetros
configurables (tamaño de bloque, factor de replicación, etc.) se leen desde el
entorno en la configuración de cada componente; aquí solo se definen las
constantes que no cambian o los valores derivados de uso general.
"""

# Cantidad de bytes en un megabyte, usada para convertir los parámetros
# expresados en MB a bytes.
BYTES_PER_MEGABYTE = 1024 * 1024

# Formato del sufijo de las partes físicas de un bloque en disco.
# Se usa relleno de ceros a tres dígitos: part001, part002, ..., part010.
BLOCK_PART_PREFIX = "part"
BLOCK_PART_PADDING = 3

# Nombre del encabezado HTTP usado para autenticar la comunicación interna
# entre nodos (distinto del JWT de los usuarios finales).
INTERNAL_API_KEY_HEADER = "X-DFSHA-API-Key"

# Longitud máxima de un nombre de usuario, archivo o directorio.
MAX_NAME_LENGTH = 255


def block_part_name(file_name: str, part_index: int) -> str:
    """Construye el nombre de una parte física de un bloque.

    Ejemplo: block_part_name("libro.pdf", 1) -> "libro.pdf-part001".
    """
    suffix = f"{BLOCK_PART_PREFIX}{part_index:0{BLOCK_PART_PADDING}d}"
    return f"{file_name}-{suffix}"
