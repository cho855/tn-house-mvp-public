
set -euo pipefail


KML_DIR="${KML_DIR:-./data/kml}"
TABLE="${TABLE:-land_sections_raw}"
DB_SERVICE="${DB_SERVICE:-db}"


if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    
    line="${line%$'\r'}"
    
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      key="${line%%=*}"
      val="${line#*=}"  
      val="$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [[ "$val" =~ ^\".*\"$ ]]; then val="${val:1:-1}"; fi
      if [[ "$val" =~ ^\'.*\'$ ]]; then val="${val:1:-1}"; fi
      export "$key=$val"
    fi
  done < .env
fi


PGHOST="${PGHOST:-$DB_SERVICE}"          
PGDATABASE="${PGDATABASE:-tn_house}"
PGUSER="${PGUSER:-tn}"
PGPASSWORD="${PGPASSWORD:-tn}"
PGPORT="${PGPORT:-5432}"


DB_CID="$(docker compose ps -q "$DB_SERVICE")"
if [ -z "$DB_CID" ]; then
  echo "❌ 找不到 docker compose 服務：$DB_SERVICE"
  echo "   請確認：docker compose ps"
  exit 1
fi

NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$DB_CID")"
if [ -z "$NET" ]; then
  echo "❌ 取得 network 失敗"
  exit 1
fi


if [ ! -d "$KML_DIR" ]; then
  echo "❌ 找不到資料夾：$KML_DIR"
  exit 1
fi

shopt -s nullglob
FILES=("$KML_DIR"/*.kml "$KML_DIR"/*.KML)
if [ ${#FILES[@]} -eq 0 ]; then
  echo "❌ $KML_DIR 內沒有 .kml 檔"
  exit 1
fi

echo "✅ Network: $NET"
echo "✅ Import ${#FILES[@]} files from: $KML_DIR"
echo "✅ Target table: $TABLE"


FIRST=1
for f in "${FILES[@]}"; do
  echo "==> importing: $f"
  if [ $FIRST -eq 1 ]; then
    MODE="-overwrite"
    FIRST=0
  else
    MODE="-append"
  fi

  docker run --rm \
    -v "$(pwd)/data:/data" \
    --network "$NET" \
    -e PGCLIENTENCODING=UTF8 \
    ghcr.io/osgeo/gdal:alpine-small-latest \
    ogr2ogr \
      -f "PostgreSQL" \
      "PG:host=${PGHOST} port=${PGPORT} dbname=${PGDATABASE} user=${PGUSER} password=${PGPASSWORD}" \
      "/data/kml/$(basename "$f")" \
      -nln "$TABLE" \
      -nlt MULTIPOLYGON \
      -lco GEOMETRY_NAME=geom \
      -lco FID=id \
      -t_srs EPSG:4326 \
      -skipfailures \
      $MODE
done

echo "🎉 Done."
echo "👉 Next: docker exec -it \$(docker compose ps -q $DB_SERVICE) psql -U $PGUSER -d $PGDATABASE -c \"\\d $TABLE\""
