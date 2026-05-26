# Alembic Commands

## Check when Alembic breaks

pwd
dir -Recurse -Filter alembic.ini
alembic -c .\infra\db\alembic.ini current

py -c "import autonomous_trading_platform; print('import ok')"
py -c "from autonomous_trading_platform.storage.sor.models.base import Base; import autonomous_trading_platform.storage.sor.models; print(list(Base.metadata.tables.keys()))"

docker ps
docker exec -it ratp_postgres psql -U ratp -d ratp -c "\dt"
