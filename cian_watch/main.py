import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial

from playwright.async_api import Error as PlaywrightError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cian_watch.browser import BrowserHost
from cian_watch.cian_search import CianSearch
from cian_watch.db.catalog import CatalogRepository
from cian_watch.db.engine import create_engine, create_sessions
from cian_watch.db.migrations import apply_migrations
from cian_watch.db.repository import OffersRepository
from cian_watch.db.tables import DealType
from cian_watch.db.users import UsersRepository
from cian_watch.db.watchlist import WatchlistRepository
from cian_watch.domclick import DomclickEstimator
from cian_watch.exceptions import (
    BlockedError,
    DomclickAddressNotFound,
    DomclickError,
    DomclickNoPrediction,
    SerpParseError,
)
from cian_watch.formatting import rubles
from cian_watch.logs import configure_logging
from cian_watch.models import (
    DomclickEstimate,
    EstimationOutcome,
    Offer,
    OfferFacts,
    PollReport,
    PriceChange,
    RentedStats,
    RentEstimate,
)
from cian_watch.proxy_rotation import ProxyRotator
from cian_watch.rent import RentEstimator
from cian_watch.telegram import TelegramNotifier
from cian_watch.telegram_api import TelegramApi
from cian_watch.telegram_bot import TelegramBot
from config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class _Defaults:
    ESTIMATES_PER_POLL: int = 30
    ESTIMATE_PAUSE_SECONDS: float = 0.5
    ESTIMATE_ATTEMPTS: int = 2
    ESTIMATE_RETRY_PAUSE_SECONDS: float = 2.0
    NOTIFY_MAX_NEW_PER_POLL: int = 40
    BACKFILL_BATCH: int = 40
    RENTED_STATS_DAYS: int = 30
    RENTED_STATS_MIN_COUNT: int = 3
    SWEEP_IDLE_SECONDS: float = 60.0


@dataclass
class _Scrapers:
    """Один браузер и прокси на всех, по контексту с собственным отпечатком на каждую задачу:
    лента (с ней же Домклик и аренда рядом), обход продажи, обход аренды."""

    host: BrowserHost
    feed: CianSearch
    sale: CianSearch
    rent: CianSearch
    domclick: DomclickEstimator
    rotator: ProxyRotator | None
    relaunch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def relaunch(self, rotate_ip: bool, seen_generation: int) -> None:
        """Полный перезапуск браузера: новые отпечатки и пустые куки у всех контекстов, при
        rotate_ip перед этим ещё и новый IP прокси. Задачи работают параллельно и могут упасть
        на одном и том же сбое почти одновременно: перезапускает только та, чьё поколение ещё
        текущее, остальные получают уже перезапущенный браузер и просто переоткрывают контекст."""
        async with self.relaunch_lock:
            if self.host.generation != seen_generation:
                return
            if rotate_ip and self.rotator is not None:
                await self.rotator.rotate()
            await self.host.restart()


@dataclass
class _Telegram:
    """Исходящая и входящая стороны одного бота поверх общего API-клиента."""

    notifier: TelegramNotifier
    bot: TelegramBot


async def watch(settings: Settings) -> None:
    """Поднимает базу (миграции до head), Telegram (если задан) и браузер, затем запускает
    параллельные задачи до остановки процесса: ленту новых, обход продажи, обход аренды
    и, при включённом Telegram, опрос апдейтов бота."""
    engine = create_engine(settings=settings)
    try:
        await apply_migrations(engine=engine)
        sessions = create_sessions(engine=engine)
        repository = OffersRepository(sessions=sessions)
        watchlist = WatchlistRepository(sessions=sessions)
        async with AsyncExitStack() as stack:
            users = UsersRepository(sessions=sessions)
            telegram = await _open_telegram(
                settings=settings, stack=stack, sessions=sessions, users=users, watchlist=watchlist
            )
            notifier = telegram.notifier if telegram is not None else None
            host = await stack.enter_async_context(BrowserHost(settings))
            proxy = settings.proxy()
            session_lock = asyncio.Lock()
            scrapers = _Scrapers(
                host=host,
                feed=CianSearch(host=host, settings=settings, role="feed", session_lock=session_lock),
                sale=CianSearch(host=host, settings=settings, role="sale sweep", session_lock=session_lock),
                rent=CianSearch(host=host, settings=settings, role="rent sweep", session_lock=session_lock),
                domclick=DomclickEstimator(host=host, settings=settings),
                rotator=ProxyRotator(rotate_url=proxy.rotate_url) if proxy and proxy.rotate_url else None,
            )
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_feed_loop(settings, scrapers, repository, notifier, watchlist))
                tasks.create_task(
                    _sweep_loop(settings, scrapers, repository, notifier, watchlist, DealType.SALE)
                )
                tasks.create_task(
                    _sweep_loop(settings, scrapers, repository, notifier, watchlist, DealType.RENT)
                )
                if telegram is not None:
                    tasks.create_task(telegram.bot.run())
    finally:
        await engine.dispose()


async def _open_telegram(
    settings: Settings,
    stack: AsyncExitStack,
    sessions: async_sessionmaker[AsyncSession],
    users: UsersRepository,
    watchlist: WatchlistRepository,
) -> _Telegram | None:
    """Без токена Telegram выключен целиком. chat_id из настроек это владелец: он заводится
    в users при старте, остальные выдаются командой cian-watch-users."""
    if settings.telegram_bot_token is None:
        logger.info("telegram is off: set CIAN_TELEGRAM_BOT_TOKEN to enable")
        return None
    if settings.telegram_chat_id is not None:
        await users.grant(
            telegram_chat_id=settings.telegram_chat_id, name="owner", created_at=datetime.now(UTC)
        )
    api = await stack.enter_async_context(
        TelegramApi(bot_token=settings.telegram_bot_token, proxy=settings.telegram_proxy)
    )
    return _Telegram(
        notifier=TelegramNotifier(api=api, recipients=users.active_chat_ids),
        bot=TelegramBot(
            api=api, users=users, watchlist=watchlist, catalog=CatalogRepository(sessions=sessions)
        ),
    )


async def _feed_loop(
    settings: Settings,
    scrapers: _Scrapers,
    repository: OffersRepository,
    notifier: TelegramNotifier | None,
    watchlist: WatchlistRepository,
) -> None:
    awaiting_estimate: set[int] = set()
    catch_up = True
    while True:
        recorded = await _poll_feed(
            settings=settings,
            scrapers=scrapers,
            repository=repository,
            notifier=notifier,
            watchlist=watchlist,
            awaiting_estimate=awaiting_estimate,
            catch_up=catch_up,
        )
        catch_up = catch_up and not recorded
        await _backfill_estimates(scrapers=scrapers, repository=repository)
        await asyncio.sleep(_next_pause(settings=settings))


async def _poll_feed(
    settings: Settings,
    scrapers: _Scrapers,
    repository: OffersRepository,
    notifier: TelegramNotifier | None,
    watchlist: WatchlistRepository,
    awaiting_estimate: set[int],
    catch_up: bool,
) -> bool:
    """Один проход ленты: новые объявления (с ранней остановкой), запись в базу, оценки Домклика
    и аренды для новых, уведомления. Первый проход после старта (catch_up) и пачка новых больше
    порога (простой парсера) только пишутся в базу: иначе после каждого перезапуска в Telegram
    улетает всё, что накопилось. Возвращает, дошёл ли проход до записи в базу: тихий проход
    засчитывается только тогда.
    Оцениваются только новые объявления этого прохода и те, что ушли в Telegram без оценки
    раньше (awaiting_estimate). Если лента не открылась даже после перезапусков или браузер
    упал, проход пропускается."""
    generation = scrapers.host.generation
    try:
        offers = await _with_browser_retries(
            settings=settings,
            scrapers=scrapers,
            cian=scrapers.feed,
            action=partial(_fetch_feed, settings=settings, scrapers=scrapers, repository=repository),
        )
        report = await repository.record(offers=offers, seen_at=datetime.now(UTC))
        quiet = catch_up or len(report.new_offers) > _Defaults.NOTIFY_MAX_NEW_PER_POLL
        _print_report(report=report, offers_total=len(offers), quiet=quiet)
        if quiet:
            return True
        new_ids = {offer.id for offer in report.new_offers}
        candidates = [
            offer.facts() for offer in offers if offer.id in new_ids or offer.id in awaiting_estimate
        ]
        outcome = await _estimate_pending(
            offers=candidates, domclick=scrapers.domclick, repository=repository
        )
        rents = await _estimate_rent(
            offers=[offer.facts() for offer in report.new_offers],
            rent_estimator=RentEstimator(cian=scrapers.feed),
            repository=repository,
        )
        if notifier is not None:
            await _notify(
                notifier=notifier,
                report=report,
                offers=offers,
                outcome=outcome,
                rents=rents,
                awaiting=awaiting_estimate,
                repository=repository,
                watchlist=watchlist,
            )
    except SerpParseError:
        logger.exception("feed could not be parsed, skipping this poll")
        return False
    except (BlockedError, PlaywrightError):
        logger.exception("feed browser path failed, relaunching and skipping this poll")
        await scrapers.relaunch(rotate_ip=True, seen_generation=generation)
        return False
    return True


async def _sweep_loop(
    settings: Settings,
    scrapers: _Scrapers,
    repository: OffersRepository,
    notifier: TelegramNotifier | None,
    watchlist: WatchlistRepository,
    deal_type: DealType,
) -> None:
    """Непрерывный обход районов с активными объявлениями по одному типу сделки: район
    за районом с паузой, пока есть давно не обходившиеся; иначе ожидание. Сбой одного района
    не останавливает обход."""
    swept_at: dict[int, datetime] = {}
    while True:
        now = datetime.now(UTC)
        due = [
            raion_id
            for raion_id in await repository.active_districts()
            if now - swept_at.get(raion_id, datetime.min.replace(tzinfo=UTC))
            >= timedelta(minutes=settings.sweep_interval_minutes)
        ]
        if not due:
            await asyncio.sleep(_Defaults.SWEEP_IDLE_SECONDS)
            continue
        for raion_id in due:
            await _sweep_district_safely(
                settings=settings,
                scrapers=scrapers,
                repository=repository,
                notifier=notifier,
                watchlist=watchlist,
                raion_id=raion_id,
                deal_type=deal_type,
            )
            swept_at[raion_id] = datetime.now(UTC)
            await asyncio.sleep(settings.sweep_pause_seconds)


async def _sweep_district_safely(
    settings: Settings,
    scrapers: _Scrapers,
    repository: OffersRepository,
    notifier: TelegramNotifier | None,
    watchlist: WatchlistRepository,
    raion_id: int,
    deal_type: DealType,
) -> None:
    generation = scrapers.host.generation
    try:
        await _sweep_district(
            settings=settings,
            scrapers=scrapers,
            repository=repository,
            notifier=notifier,
            watchlist=watchlist,
            raion_id=raion_id,
            deal_type=deal_type,
        )
    except SerpParseError:
        logger.exception("sweep of raion %d %s could not be parsed, skipped", raion_id, deal_type)
    except (BlockedError, PlaywrightError):
        logger.exception("sweep of raion %d %s failed, relaunching browser", raion_id, deal_type)
        await scrapers.relaunch(rotate_ip=True, seen_generation=generation)


async def _with_browser_retries[T](
    settings: Settings, scrapers: _Scrapers, cian: CianSearch, action: Callable[[], Awaitable[T]]
) -> T:
    """Выполняет действие с выдачей и чинит сбои ступенчато. Первый обрыв или таймаут лечится
    переоткрытием только своего контекста: браузер общий с другими задачами, и перезапускать
    его из-за одного запроса значит срывать их работу. Капча или повторный сбой подряд
    означают, что плох весь браузер или IP: тогда перезапуск целиком, со второй попытки
    ещё и со сменой IP прокси. Пауза растёт с номером попытки, после последней ошибка уходит наверх."""
    attempt = 1
    while True:
        generation = scrapers.host.generation
        try:
            return await action()
        except (BlockedError, PlaywrightError) as exc:
            if attempt >= settings.retry_attempts:
                raise
            delay = settings.retry_delay_seconds * attempt
            full_relaunch = isinstance(exc, BlockedError) or attempt >= 2
            rotate_ip = isinstance(exc, BlockedError) or attempt >= 3
            logger.warning(
                "search failed (%s: %s), %s, try %d/%d, next in %ds",
                type(exc).__name__,
                str(exc).splitlines()[0][:120],
                "relaunching browser" + (" with new proxy ip" if rotate_ip else "")
                if full_relaunch
                else "reopening own context",
                attempt,
                settings.retry_attempts,
                delay,
            )
            if full_relaunch:
                await scrapers.relaunch(rotate_ip=rotate_ip, seen_generation=generation)
            else:
                await cian.close()
            await asyncio.sleep(delay)
            attempt += 1


async def _fetch_feed(settings: Settings, scrapers: _Scrapers, repository: OffersRepository) -> list[Offer]:
    """Лента по дате добавления страница за страницей, пока на странице есть хоть одно
    неизвестное объявление: дальше всё старое. Известные объявления со просмотренных страниц
    тоже возвращаются, их цены обновятся бесплатно."""
    collected: dict[int, Offer] = {}
    for page_number in range(1, settings.pages + 1):
        page_offers = await scrapers.feed.fetch_page(page_number=page_number)
        for offer in page_offers:
            collected.setdefault(offer.id, offer)
        known = await repository.known_ids(ids=[offer.id for offer in page_offers])
        if not page_offers or all(offer.id in known for offer in page_offers):
            break
    return list(collected.values())


async def _sweep_district(
    settings: Settings,
    scrapers: _Scrapers,
    repository: OffersRepository,
    notifier: TelegramNotifier | None,
    watchlist: WatchlistRepository,
    raion_id: int,
    deal_type: DealType,
) -> None:
    """Район целиком по одному типу сделки: продажа по фильтру из настроек, аренда вся
    долгосрочная. Цены обновляются, пропавшие помечаются; по продаже наблюдатели объявления
    получают изменение цены, снятие и возвращение."""
    started = time.monotonic()
    if deal_type is DealType.SALE:
        cian = scrapers.sale
        action = partial(cian.fetch_district, raion_id=raion_id, max_pages=settings.sweep_pages_max)
    else:
        cian = scrapers.rent
        rent_query = CianSearch.with_district(RentEstimator.district_base_query(), raion_id)
        action = partial(cian.fetch_all, json_query=rent_query, max_pages=settings.sweep_pages_max)
    offers = await _with_browser_retries(settings=settings, scrapers=scrapers, cian=cian, action=action)
    seen_at = datetime.now(UTC)
    report = await repository.record(offers=offers, seen_at=seen_at, deal_type=deal_type)
    gone = await repository.register_sweep(
        raion_id=raion_id,
        deal_type=deal_type,
        seen_ids={offer.id for offer in offers},
        swept_at=seen_at,
        misses_to_sell=settings.sweep_misses_to_sell,
    )
    logger.info(
        "sweep raion %d %s: %d offers, %d new, %d price changes, %d gone, %d reappeared in %.1fs",
        raion_id,
        deal_type,
        len(offers),
        len(report.new_offers),
        len(report.price_changes),
        len(gone),
        len(report.reappeared),
        time.monotonic() - started,
    )
    if deal_type is not DealType.SALE:
        return
    for change in report.price_changes:
        _print_price_change(change=change)
    if notifier is not None:
        await _alert_watchers(
            notifier=notifier, watchlist=watchlist, repository=repository, report=report, gone_ids=gone
        )


async def _notify(
    notifier: TelegramNotifier,
    report: PollReport,
    offers: list[Offer],
    outcome: EstimationOutcome,
    rents: dict[int, RentEstimate],
    awaiting: set[int],
    repository: OffersRepository,
    watchlist: WatchlistRepository,
) -> None:
    """Новые объявления уходят всем одним сообщением с оценкой, если она получена в этом проходе,
    иначе объявление запоминается в awaiting. Оценка, догнавшая такое объявление позже, уходит
    отдельным сообщением; объявления, которых в Telegram не было, догоняющих сообщений не получают.
    Изменения цены и возвращения уходят только наблюдателям."""
    new_ids = {offer.id for offer in report.new_offers}
    for offer in sorted(report.new_offers, key=lambda offer: offer.creation_date):
        estimate = outcome.estimates.get(offer.id, None)
        house_not_found = offer.id in outcome.not_found
        await notifier.notify_new_offer(
            offer=offer,
            estimate=estimate,
            house_not_found=house_not_found,
            rent=rents.get(offer.id),
            rented=await _rented_stats(offer=offer, repository=repository),
        )
        if estimate is None and not house_not_found:
            awaiting.add(offer.id)
    for offer in offers:
        if offer.id in awaiting and offer.id not in new_ids and offer.id in outcome.estimates:
            await notifier.notify_late_estimate(offer=offer, estimate=outcome.estimates[offer.id])
    awaiting.difference_update(outcome.estimates, outcome.not_found)
    await _alert_watchers(
        notifier=notifier, watchlist=watchlist, repository=repository, report=report, gone_ids=[]
    )


async def _alert_watchers(
    notifier: TelegramNotifier,
    watchlist: WatchlistRepository,
    repository: OffersRepository,
    report: PollReport,
    gone_ids: list[int],
) -> None:
    """Алерты по объявлению только тем, кто нажал «Отслеживать»: любое изменение цены,
    снятие с продажи, возвращение. Объявления без наблюдателей пропускаются молча."""
    offer_ids = [change.offer.id for change in report.price_changes]
    offer_ids += [offer.id for offer in report.reappeared]
    offer_ids += gone_ids
    watchers = await watchlist.watchers(offer_ids=offer_ids)
    if not watchers:
        return
    for change in report.price_changes:
        if change.offer.id in watchers:
            await notifier.notify_price_change(change=change, chat_ids=watchers[change.offer.id])
    for offer in report.reappeared:
        if offer.id in watchers:
            await notifier.notify_reappeared(offer=offer, chat_ids=watchers[offer.id])
    for row in await repository.rows_by_ids(ids=[offer_id for offer_id in gone_ids if offer_id in watchers]):
        await notifier.notify_gone(row=row, chat_ids=watchers[row.id])


async def _backfill_estimates(scrapers: _Scrapers, repository: OffersRepository) -> None:
    """Досчёт Домклика и аренды для объявлений, попавших в базу без оценок (тихий проход,
    обход района): пачка между проходами ленты, теми же контекстами, что и лента. Сбои
    оценщиков гасятся внутри, следующая пачка возьмёт то, что не досчиталось."""
    pending = await repository.offers_to_backfill(limit=_Defaults.BACKFILL_BATCH)
    if not pending:
        return
    started = time.monotonic()
    outcome = await _estimate_pending(offers=pending, domclick=scrapers.domclick, repository=repository)
    rents = await _estimate_rent(
        offers=pending, rent_estimator=RentEstimator(cian=scrapers.feed), repository=repository
    )
    logger.info(
        "backfill: %d offers, %d domclick estimates, %d rent estimates in %.0fs",
        len(pending),
        len(outcome.estimates),
        len(rents),
        time.monotonic() - started,
    )


async def _estimate_pending(
    offers: list[OfferFacts], domclick: DomclickEstimator, repository: OffersRepository
) -> EstimationOutcome:
    """Оценивает объявления выдачи без строки в domclick_estimates, с паузой между запросами,
    чтобы Qrator не счёл это атакой. Ненайденный адрес записывается как ошибка и больше
    не повторяется. Если Домклик не ответил даже после повтора, остаток пачки откладывается
    до следующего опроса, но сам проход продолжается: уведомления должны уйти и без оценки."""
    outcome = EstimationOutcome()
    pending = await repository.offers_without_estimate(offers=offers, limit=_Defaults.ESTIMATES_PER_POLL)
    for offer in pending:
        try:
            estimate = await _estimate_with_retry(domclick=domclick, offer=offer)
        except (DomclickAddressNotFound, DomclickNoPrediction) as exc:
            logger.warning("domclick can not estimate offer %s: %s", offer.id, exc)
            await repository.save_estimate_failure(
                offer_id=offer.id, error=str(exc), estimated_at=datetime.now(UTC)
            )
            outcome.not_found.add(offer.id)
            continue
        if estimate is None:
            logger.warning("domclick is not answering, estimates postponed to next poll")
            break
        await repository.save_estimate(estimate=estimate, estimated_at=datetime.now(UTC))
        outcome.estimates[offer.id] = estimate
        _print_estimate(offer=offer, estimate=estimate)
        await asyncio.sleep(_Defaults.ESTIMATE_PAUSE_SECONDS)
    return outcome


async def _estimate_with_retry(domclick: DomclickEstimator, offer: OfferFacts) -> DomclickEstimate | None:
    """Таймаут, обрыв или 5xx от Домклика лечатся переоткрытием его контекста (новые куки Qrator)
    и одним повтором. Ненайденный адрес не повторяется и уходит наверх."""
    for attempt in range(1, _Defaults.ESTIMATE_ATTEMPTS + 1):
        try:
            return await domclick.estimate(offer=offer)
        except (DomclickAddressNotFound, DomclickNoPrediction):
            raise
        except (PlaywrightError, DomclickError) as exc:
            logger.warning(
                "domclick failed for offer %s, try %d/%d: %s",
                offer.id,
                attempt,
                _Defaults.ESTIMATE_ATTEMPTS,
                exc,
            )
            await domclick.close()
            await asyncio.sleep(_Defaults.ESTIMATE_RETRY_PAUSE_SECONDS)
    return None


def _print_report(report: PollReport, offers_total: int, quiet: bool) -> None:
    if quiet:
        logger.info(
            "bulk catch-up: %d offers seen, %d new stored silently, %d price changes",
            offers_total,
            len(report.new_offers),
            len(report.price_changes),
        )
        return
    logger.info(
        "search page polled: %d offers, %d new, %d price changes",
        offers_total,
        len(report.new_offers),
        len(report.price_changes),
    )
    for offer in sorted(report.new_offers, key=lambda offer: offer.creation_date):
        logger.info(
            "NEW %s | %s | %s | %s | %s",
            offer.creation_date.strftime("%d.%m %H:%M"),
            offer.formatted_full_price,
            offer.formatted_full_info,
            offer.address,
            offer.url,
        )
    for change in report.price_changes:
        _print_price_change(change=change)


def _print_price_change(change: PriceChange) -> None:
    logger.info(
        "PRICE %s -> %s | %s | %s | %s",
        rubles(change.old_price),
        rubles(change.offer.price),
        change.offer.formatted_full_info,
        change.offer.address,
        change.offer.url,
    )


async def _rented_stats(offer: Offer, repository: OffersRepository) -> RentedStats | None:
    """Что сдалось в районе объявления за последний месяц; пока накоплено меньше трёх, не показываем."""
    raion = offer.geo.raion()
    if raion is None:
        return None
    stats = await repository.rented_stats(
        raion_id=raion.id, days=_Defaults.RENTED_STATS_DAYS, now=datetime.now(UTC)
    )
    if stats is None or stats.count < _Defaults.RENTED_STATS_MIN_COUNT:
        return None
    return stats


async def _estimate_rent(
    offers: list[OfferFacts], rent_estimator: RentEstimator, repository: OffersRepository
) -> dict[int, RentEstimate]:
    """Аренда похожих квартир вокруг каждого объявления без оценки, до трёх запросов к API
    Циана на штуку. Пустой результат тоже записывается, чтобы не искать заново. Сбой выдачи
    здесь не роняет проход: аренда вторична, объявление уйдёт без неё."""
    rents: dict[int, RentEstimate] = {}
    pending = await repository.offers_without_rent(offers=offers, limit=_Defaults.ESTIMATES_PER_POLL)
    for offer in pending:
        try:
            rent = await rent_estimator.estimate(offer=offer)
        except (BlockedError, SerpParseError, PlaywrightError) as exc:
            logger.warning("rent lookup failed for offer %s, skipping the rest: %s", offer.id, exc)
            break
        if rent is None:
            logger.info("no rent comparables for offer %s", offer.id)
            await repository.save_rent_missing(offer_id=offer.id, estimated_at=datetime.now(UTC))
            continue
        await repository.save_rent(rent=rent, estimated_at=datetime.now(UTC))
        rents[offer.id] = rent
        logger.info(
            "RENT %s/month | %d similar, %s | payback %.0f years | %s",
            rubles(rent.median_rent),
            rent.sample_size,
            rent.scope,
            offer.price / (rent.median_rent * 12),
            offer.url,
        )
    return rents


def _print_estimate(offer: OfferFacts, estimate: DomclickEstimate) -> None:
    gap_percent = (offer.price - estimate.market_price) / estimate.market_price * 100
    logger.info(
        "DOMCLICK %s (%s - %s) | cian %s, %+.0f%% to market | %s | %s",
        rubles(estimate.market_price),
        rubles(estimate.min_price),
        rubles(estimate.max_price),
        rubles(offer.price),
        gap_percent,
        offer.url,
        estimate.url,
    )


def _next_pause(settings: Settings) -> float:
    jitter = random.uniform(0, settings.poll_jitter_seconds)  # noqa: S311
    return settings.poll_interval_seconds + jitter


def run() -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from CIAN_* env
    configure_logging(settings=settings)
    try:
        asyncio.run(watch(settings=settings))
    except KeyboardInterrupt:
        logger.info("stopped")


if __name__ == "__main__":
    run()
