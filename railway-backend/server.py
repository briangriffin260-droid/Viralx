from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, UploadFile, File, Form, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timedelta
import jwt
import bcrypt
import base64
import stripe

ROOT_DIR = Path(__file__).parent
STATIC_DIR = ROOT_DIR / 'static'
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'alltogether_db')]

# JWT Settings
JWT_SECRET = os.environ.get('JWT_SECRET', 'alltogether-super-secret-key-2025')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 72

# Stripe Settings
STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY')
stripe.api_key = STRIPE_API_KEY

# Platform Fee Settings
PLATFORM_FEE_PERCENT = 15  # 15% platform fee

# Create the main app
app = FastAPI(title="AllTogether API", version="2.0.0")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer()

# ============== MODELS ==============

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    username: str
    display_name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserProfile(BaseModel):
    id: str
    email: str
    username: str
    display_name: str
    bio: Optional[str] = ""
    avatar: Optional[str] = None
    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0
    is_premium_creator: bool = False
    subscription_price: float = 0.0
    created_at: datetime

class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None
    is_premium_creator: Optional[bool] = None
    subscription_price: Optional[float] = None

class PostCreate(BaseModel):
    content: str
    media: Optional[str] = None  # base64 image/video
    media_type: Optional[str] = "image"  # image, video
    is_premium: bool = False

class Post(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    user_avatar: Optional[str] = None
    content: str
    media: Optional[str] = None
    media_type: Optional[str] = "image"
    is_premium: bool = False
    likes_count: int = 0
    comments_count: int = 0
    created_at: datetime
    is_liked: bool = False
    is_accessible: bool = True  # Whether user can view (for premium content)
    is_boosted: bool = False  # Whether post is currently boosted

class CommentCreate(BaseModel):
    content: str

class Comment(BaseModel):
    id: str
    post_id: str
    user_id: str
    username: str
    display_name: str
    user_avatar: Optional[str] = None
    content: str
    created_at: datetime

class StoryCreate(BaseModel):
    media: str  # base64 image/video
    media_type: str = "image"

class Story(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    user_avatar: Optional[str] = None
    media: str
    media_type: str
    created_at: datetime
    expires_at: datetime
    views_count: int = 0

class StoryGroup(BaseModel):
    user_id: str
    username: str
    display_name: str
    user_avatar: Optional[str] = None
    stories: List[Story]
    has_unseen: bool = True

class AuthResponse(BaseModel):
    token: str
    user: UserProfile

class FollowStatus(BaseModel):
    is_following: bool

# ============== NEW MODELS FOR FEATURES ==============

class Notification(BaseModel):
    id: str
    user_id: str
    type: str  # like, comment, follow, message, subscription, tip
    from_user_id: str
    from_username: str
    from_display_name: str
    from_avatar: Optional[str] = None
    message: str
    reference_id: Optional[str] = None  # post_id, conversation_id, etc.
    is_read: bool = False
    created_at: datetime

class ConversationPreview(BaseModel):
    id: str
    participant_id: str
    participant_username: str
    participant_display_name: str
    participant_avatar: Optional[str] = None
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = 0

class Message(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_username: str
    sender_display_name: str
    sender_avatar: Optional[str] = None
    content: str
    media: Optional[str] = None
    media_type: Optional[str] = None
    created_at: datetime
    is_read: bool = False

class MessageCreate(BaseModel):
    content: str
    media: Optional[str] = None
    media_type: Optional[str] = None

class Subscription(BaseModel):
    id: str
    subscriber_id: str
    creator_id: str
    creator_username: str
    creator_display_name: str
    creator_avatar: Optional[str] = None
    price: float
    status: str  # active, cancelled, expired
    created_at: datetime
    expires_at: datetime

class TipCreate(BaseModel):
    amount: float
    message: Optional[str] = ""

class Tip(BaseModel):
    id: str
    from_user_id: str
    from_username: str
    to_user_id: str
    to_username: str
    amount: float
    message: str
    created_at: datetime

class PaymentIntent(BaseModel):
    id: str
    amount: float
    currency: str = "usd"
    status: str  # pending, succeeded, failed
    type: str  # subscription, tip
    created_at: datetime

# ============== ID VERIFICATION MODELS ==============

class IDVerification(BaseModel):
    id: str
    user_id: str
    id_image: str  # base64 image of ID
    selfie_image: Optional[str] = None  # Optional selfie for matching
    status: str  # pending, approved, rejected
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

class IDVerificationSubmit(BaseModel):
    id_image: str  # base64 image

# ============== MODERATION & REPORTS MODELS ==============

class UserReport(BaseModel):
    id: str
    reporter_id: str
    reporter_username: str
    reported_user_id: str
    reported_username: str
    reason: str
    content_type: str  # comment, message, profile
    content_id: Optional[str] = None
    content_text: Optional[str] = None
    status: str  # pending, reviewed, action_taken, dismissed
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    action_taken: Optional[str] = None

class ReportCreate(BaseModel):
    reported_user_id: str
    reason: str
    content_type: str  # comment, message, profile
    content_id: Optional[str] = None
    content_text: Optional[str] = None

class UserBan(BaseModel):
    id: str
    user_id: str
    username: str
    reason: str
    banned_at: datetime
    ban_expires_at: datetime
    banned_by: str  # system or admin user id
    is_active: bool = True

class ModerationResult(BaseModel):
    is_appropriate: bool
    confidence: float
    reason: Optional[str] = None
    flagged_content: Optional[str] = None

# ============== PAYMENT & STRIPE MODELS ==============

class PaymentTransaction(BaseModel):
    id: str
    user_id: str
    creator_id: Optional[str] = None
    session_id: str
    payment_type: str  # subscription, tip
    amount: float
    platform_fee: float
    creator_amount: float
    currency: str = "usd"
    status: str  # pending, paid, failed, expired
    metadata: Optional[Dict[str, str]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

class CheckoutRequest(BaseModel):
    creator_id: str
    payment_type: str  # subscription, tip
    tip_amount: Optional[float] = None  # Only for tips
    origin_url: str

# ============== ADVERTISING MODELS ==============

class Ad(BaseModel):
    id: str
    advertiser_id: str
    advertiser_name: str
    ad_type: str  # banner, sponsored_post
    title: str
    content: str
    media: Optional[str] = None
    link_url: str
    cta_text: str = "Learn More"
    impressions: int = 0
    clicks: int = 0
    budget: float
    spent: float = 0.0
    cpm_rate: float = 5.0  # Cost per 1000 impressions
    cpc_rate: float = 0.50  # Cost per click
    is_active: bool = True
    start_date: datetime

# ============== ADDITIONAL MONETIZATION MODELS ==============

# 1. Verified Badges - $4.99/month
class VerifiedBadge(BaseModel):
    id: str
    user_id: str
    status: str  # active, expired, cancelled
    price: float = 4.99
    started_at: datetime
    expires_at: datetime
    auto_renew: bool = True

class VerifiedBadgeRequest(BaseModel):
    origin_url: str

# 2. Boosted Posts - Pay to promote posts
class BoostedPost(BaseModel):
    id: str
    post_id: str
    user_id: str
    budget: float
    spent: float = 0.0
    impressions: int = 0
    clicks: int = 0
    cpm_rate: float = 3.0  # $3 per 1000 impressions
    status: str  # active, paused, completed, expired
    start_date: datetime
    end_date: datetime
    target_impressions: int

class BoostPostRequest(BaseModel):
    post_id: str
    budget: float  # Minimum $5
    days: int = 7
    origin_url: str

# 3. Virtual Gifts/Coins
class VirtualGift(BaseModel):
    id: str
    name: str
    icon: str
    coin_cost: int
    dollar_value: float
    creator_earnings: float  # 70% of dollar value

class CoinPackage(BaseModel):
    id: str
    coins: int
    price: float
    bonus_coins: int = 0

class UserCoins(BaseModel):
    user_id: str
    balance: int = 0
    total_purchased: int = 0
    total_spent: int = 0

class SendGiftRequest(BaseModel):
    recipient_id: str
    gift_id: str
    post_id: Optional[str] = None
    message: Optional[str] = None

class BuyCoinsRequest(BaseModel):
    package_id: str
    origin_url: str

# 4. Featured Spots - Creators pay to be featured
class FeaturedSpot(BaseModel):
    id: str
    user_id: str
    spot_type: str  # explore_top, explore_creators, suggested
    price: float
    start_date: datetime
    end_date: datetime
    impressions: int = 0
    clicks: int = 0
    status: str  # active, expired, cancelled

class FeaturedSpotRequest(BaseModel):
    spot_type: str
    days: int = 7
    origin_url: str

# 5. Premium Analytics
class PremiumAnalytics(BaseModel):
    id: str
    user_id: str
    plan: str  # basic, pro, enterprise
    price: float
    status: str
    started_at: datetime
    expires_at: datetime

class AnalyticsRequest(BaseModel):
    plan: str  # basic ($9.99), pro ($19.99), enterprise ($49.99)
    origin_url: str

# 6. Promoted Profiles
class PromotedProfile(BaseModel):
    id: str
    user_id: str
    budget: float
    spent: float = 0.0
    impressions: int = 0
    profile_visits: int = 0
    new_followers: int = 0
    cpm_rate: float = 4.0
    status: str
    start_date: datetime
    end_date: datetime

class PromoteProfileRequest(BaseModel):
    budget: float
    duration_days: int = 7
    origin_url: str
    target_audience: Optional[Dict] = None  # For future targeting

class AdCreate(BaseModel):
    ad_type: str  # banner, sponsored_post
    title: str
    content: str
    media: Optional[str] = None
    link_url: str
    cta_text: str = "Learn More"
    budget: float
    days_to_run: int = 7

class AdImpression(BaseModel):
    id: str
    ad_id: str
    user_id: Optional[str] = None
    created_at: datetime

class AdClick(BaseModel):
    id: str
    ad_id: str
    user_id: Optional[str] = None
    created_at: datetime

# ============== HELPER FUNCTIONS ==============

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))) -> Optional[dict]:
    if not credentials:
        return None
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        user = await db.users.find_one({"id": user_id})
        return user
    except:
        return None

async def create_notification(user_id: str, notification_type: str, from_user: dict, message: str, reference_id: str = None):
    """Helper to create notifications"""
    if user_id == from_user["id"]:
        return  # Don't notify yourself
    
    notification = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "type": notification_type,
        "from_user_id": from_user["id"],
        "from_username": from_user["username"],
        "from_display_name": from_user["display_name"],
        "from_avatar": from_user.get("avatar"),
        "message": message,
        "reference_id": reference_id,
        "is_read": False,
        "created_at": datetime.utcnow()
    }
    await db.notifications.insert_one(notification)

async def check_subscription(subscriber_id: str, creator_id: str) -> bool:
    """Check if user is subscribed to a creator"""
    subscription = await db.subscriptions.find_one({
        "subscriber_id": subscriber_id,
        "creator_id": creator_id,
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    return subscription is not None

async def check_user_banned(user_id: str) -> dict:
    """Check if user is currently banned"""
    ban = await db.user_bans.find_one({
        "user_id": user_id,
        "is_active": True,
        "ban_expires_at": {"$gt": datetime.utcnow()}
    })
    return ban

async def check_id_verified(user_id: str) -> bool:
    """Check if user has verified their ID"""
    verification = await db.id_verifications.find_one({
        "user_id": user_id,
        "status": "approved"
    })
    return verification is not None

async def ban_user(user_id: str, reason: str, banned_by: str = "system"):
    """Ban a user for 1 month"""
    user = await db.users.find_one({"id": user_id})
    if not user:
        return None
    
    ban = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "username": user["username"],
        "reason": reason,
        "banned_at": datetime.utcnow(),
        "ban_expires_at": datetime.utcnow() + timedelta(days=30),
        "banned_by": banned_by,
        "is_active": True
    }
    await db.user_bans.insert_one(ban)
    return ban

# AI Moderation using Emergent LLM
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY', 'sk-emergent-416994801D4B67e8aA')

async def moderate_content_ai(content: str) -> dict:
    """Use AI to check if content is appropriate"""
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {EMERGENT_LLM_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": """You are a content moderation AI. Analyze the following text and determine if it contains:
- Harassment or bullying
- Hate speech or discrimination
- Threats or violence
- Severe profanity or abuse directed at someone
- Disrespectful or degrading language toward creators

Respond ONLY with a JSON object in this exact format:
{"is_appropriate": true/false, "confidence": 0.0-1.0, "reason": "brief explanation if inappropriate", "flagged_content": "the problematic part if any"}

Be strict about protecting creators from harassment, but allow normal conversation and mild disagreements."""
                        },
                        {
                            "role": "user",
                            "content": f"Analyze this content: {content}"
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result["choices"][0]["message"]["content"]
                # Parse the JSON response
                import json
                try:
                    moderation_result = json.loads(ai_response)
                    return moderation_result
                except:
                    # If parsing fails, assume appropriate
                    return {"is_appropriate": True, "confidence": 0.5, "reason": None, "flagged_content": None}
            else:
                # If API fails, use basic keyword check as fallback
                return await moderate_content_basic(content)
    except Exception as e:
        logging.error(f"AI moderation error: {e}")
        return await moderate_content_basic(content)

async def moderate_content_basic(content: str) -> dict:
    """Basic keyword-based moderation as fallback"""
    offensive_keywords = [
        "fuck you", "kill yourself", "kys", "die", "ugly", "whore", "slut", 
        "bitch", "retard", "idiot", "stupid", "hate you", "worthless",
        "kill", "murder", "rape", "loser", "trash", "garbage"
    ]
    
    content_lower = content.lower()
    for keyword in offensive_keywords:
        if keyword in content_lower:
            return {
                "is_appropriate": False,
                "confidence": 0.9,
                "reason": f"Contains offensive language",
                "flagged_content": keyword
            }
    
    return {"is_appropriate": True, "confidence": 0.8, "reason": None, "flagged_content": None}

# ============== AUTH ROUTES ==============

@api_router.post("/auth/register", response_model=AuthResponse)
async def register(user_data: UserCreate):
    existing_email = await db.users.find_one({"email": user_data.email})
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    existing_username = await db.users.find_one({"username": user_data.username.lower()})
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "email": user_data.email,
        "username": user_data.username.lower(),
        "display_name": user_data.display_name,
        "password_hash": hash_password(user_data.password),
        "bio": "",
        "avatar": None,
        "followers_count": 0,
        "following_count": 0,
        "posts_count": 0,
        "is_premium_creator": False,
        "subscription_price": 9.99,
        "balance": 0.0,
        "created_at": datetime.utcnow()
    }
    
    await db.users.insert_one(user_doc)
    
    token = create_token(user_id)
    user_profile = UserProfile(
        id=user_doc["id"],
        email=user_doc["email"],
        username=user_doc["username"],
        display_name=user_doc["display_name"],
        bio=user_doc["bio"],
        avatar=user_doc["avatar"],
        followers_count=user_doc["followers_count"],
        following_count=user_doc["following_count"],
        posts_count=user_doc["posts_count"],
        is_premium_creator=user_doc["is_premium_creator"],
        subscription_price=user_doc["subscription_price"],
        created_at=user_doc["created_at"]
    )
    
    return AuthResponse(token=token, user=user_profile)

@api_router.post("/auth/login", response_model=AuthResponse)
async def login(login_data: UserLogin):
    user = await db.users.find_one({"email": login_data.email})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not verify_password(login_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_token(user["id"])
    user_profile = UserProfile(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        display_name=user["display_name"],
        bio=user.get("bio", ""),
        avatar=user.get("avatar"),
        followers_count=user.get("followers_count", 0),
        following_count=user.get("following_count", 0),
        posts_count=user.get("posts_count", 0),
        is_premium_creator=user.get("is_premium_creator", False),
        subscription_price=user.get("subscription_price", 9.99),
        created_at=user["created_at"]
    )
    
    return AuthResponse(token=token, user=user_profile)

@api_router.get("/auth/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserProfile(
        id=current_user["id"],
        email=current_user["email"],
        username=current_user["username"],
        display_name=current_user["display_name"],
        bio=current_user.get("bio", ""),
        avatar=current_user.get("avatar"),
        followers_count=current_user.get("followers_count", 0),
        following_count=current_user.get("following_count", 0),
        posts_count=current_user.get("posts_count", 0),
        is_premium_creator=current_user.get("is_premium_creator", False),
        subscription_price=current_user.get("subscription_price", 9.99),
        created_at=current_user["created_at"]
    )

@api_router.put("/auth/profile", response_model=UserProfile)
async def update_profile(update_data: UserProfileUpdate, current_user: dict = Depends(get_current_user)):
    update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
    
    if update_dict:
        await db.users.update_one({"id": current_user["id"]}, {"$set": update_dict})
    
    updated_user = await db.users.find_one({"id": current_user["id"]})
    return UserProfile(
        id=updated_user["id"],
        email=updated_user["email"],
        username=updated_user["username"],
        display_name=updated_user["display_name"],
        bio=updated_user.get("bio", ""),
        avatar=updated_user.get("avatar"),
        followers_count=updated_user.get("followers_count", 0),
        following_count=updated_user.get("following_count", 0),
        posts_count=updated_user.get("posts_count", 0),
        is_premium_creator=updated_user.get("is_premium_creator", False),
        subscription_price=updated_user.get("subscription_price", 9.99),
        created_at=updated_user["created_at"]
    )

# ============== USER ROUTES ==============

@api_router.get("/users/{username}", response_model=UserProfile)
async def get_user_profile(username: str):
    user = await db.users.find_one({"username": username.lower()})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserProfile(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        display_name=user["display_name"],
        bio=user.get("bio", ""),
        avatar=user.get("avatar"),
        followers_count=user.get("followers_count", 0),
        following_count=user.get("following_count", 0),
        posts_count=user.get("posts_count", 0),
        is_premium_creator=user.get("is_premium_creator", False),
        subscription_price=user.get("subscription_price", 9.99),
        created_at=user["created_at"]
    )

@api_router.get("/users/{user_id}/follow-status", response_model=FollowStatus)
async def get_follow_status(user_id: str, current_user: dict = Depends(get_current_user)):
    follow = await db.follows.find_one({
        "follower_id": current_user["id"],
        "following_id": user_id
    })
    return FollowStatus(is_following=follow is not None)

@api_router.post("/users/{user_id}/follow")
async def follow_user(user_id: str, current_user: dict = Depends(get_current_user)):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")
    
    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    existing_follow = await db.follows.find_one({
        "follower_id": current_user["id"],
        "following_id": user_id
    })
    
    if existing_follow:
        raise HTTPException(status_code=400, detail="Already following this user")
    
    follow_doc = {
        "id": str(uuid.uuid4()),
        "follower_id": current_user["id"],
        "following_id": user_id,
        "created_at": datetime.utcnow()
    }
    
    await db.follows.insert_one(follow_doc)
    await db.users.update_one({"id": current_user["id"]}, {"$inc": {"following_count": 1}})
    await db.users.update_one({"id": user_id}, {"$inc": {"followers_count": 1}})
    
    # Create notification
    await create_notification(
        user_id=user_id,
        notification_type="follow",
        from_user=current_user,
        message="started following you"
    )
    
    return {"message": "Successfully followed user"}

@api_router.delete("/users/{user_id}/follow")
async def unfollow_user(user_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.follows.delete_one({
        "follower_id": current_user["id"],
        "following_id": user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=400, detail="Not following this user")
    
    await db.users.update_one({"id": current_user["id"]}, {"$inc": {"following_count": -1}})
    await db.users.update_one({"id": user_id}, {"$inc": {"followers_count": -1}})
    
    return {"message": "Successfully unfollowed user"}

@api_router.get("/users/{user_id}/followers", response_model=List[UserProfile])
async def get_followers(user_id: str, skip: int = 0, limit: int = 20):
    follows = await db.follows.find({"following_id": user_id}).skip(skip).limit(limit).to_list(limit)
    follower_ids = [f["follower_id"] for f in follows]
    
    users = await db.users.find({"id": {"$in": follower_ids}}).to_list(limit)
    return [UserProfile(
        id=u["id"],
        email=u["email"],
        username=u["username"],
        display_name=u["display_name"],
        bio=u.get("bio", ""),
        avatar=u.get("avatar"),
        followers_count=u.get("followers_count", 0),
        following_count=u.get("following_count", 0),
        posts_count=u.get("posts_count", 0),
        is_premium_creator=u.get("is_premium_creator", False),
        subscription_price=u.get("subscription_price", 9.99),
        created_at=u["created_at"]
    ) for u in users]

@api_router.get("/users/{user_id}/following", response_model=List[UserProfile])
async def get_following(user_id: str, skip: int = 0, limit: int = 20):
    follows = await db.follows.find({"follower_id": user_id}).skip(skip).limit(limit).to_list(limit)
    following_ids = [f["following_id"] for f in follows]
    
    users = await db.users.find({"id": {"$in": following_ids}}).to_list(limit)
    return [UserProfile(
        id=u["id"],
        email=u["email"],
        username=u["username"],
        display_name=u["display_name"],
        bio=u.get("bio", ""),
        avatar=u.get("avatar"),
        followers_count=u.get("followers_count", 0),
        following_count=u.get("following_count", 0),
        posts_count=u.get("posts_count", 0),
        is_premium_creator=u.get("is_premium_creator", False),
        subscription_price=u.get("subscription_price", 9.99),
        created_at=u["created_at"]
    ) for u in users]

# ============== POST ROUTES ==============

@api_router.post("/posts", response_model=Post)
async def create_post(post_data: PostCreate, current_user: dict = Depends(get_current_user)):
    post_id = str(uuid.uuid4())
    post_doc = {
        "id": post_id,
        "user_id": current_user["id"],
        "username": current_user["username"],
        "display_name": current_user["display_name"],
        "user_avatar": current_user.get("avatar"),
        "content": post_data.content,
        "media": post_data.media,
        "media_type": post_data.media_type,
        "is_premium": post_data.is_premium,
        "likes_count": 0,
        "comments_count": 0,
        "created_at": datetime.utcnow()
    }
    
    await db.posts.insert_one(post_doc)
    await db.users.update_one({"id": current_user["id"]}, {"$inc": {"posts_count": 1}})
    
    return Post(**post_doc, is_liked=False, is_accessible=True)

@api_router.get("/posts", response_model=List[Post])
async def get_feed(skip: int = 0, limit: int = 20, current_user: dict = Depends(get_optional_user)):
    posts = await db.posts.find().sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    
    result = []
    for post in posts:
        is_liked = False
        is_accessible = True
        
        if current_user:
            like = await db.likes.find_one({
                "user_id": current_user["id"],
                "post_id": post["id"]
            })
            is_liked = like is not None
            
            # Check premium access
            if post.get("is_premium") and post["user_id"] != current_user["id"]:
                is_accessible = await check_subscription(current_user["id"], post["user_id"])
        elif post.get("is_premium"):
            is_accessible = False
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"] if is_accessible else "Premium content - Subscribe to view",
            media=post.get("media") if is_accessible else None,
            media_type=post.get("media_type", "image"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=is_liked,
            is_accessible=is_accessible
        ))
    
    return result

@api_router.get("/posts/videos", response_model=List[Post])
async def get_video_feed(skip: int = 0, limit: int = 20, current_user: dict = Depends(get_optional_user)):
    """TikTok-style video feed"""
    posts = await db.posts.find({"media_type": "video"}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    
    result = []
    for post in posts:
        is_liked = False
        is_accessible = True
        
        if current_user:
            like = await db.likes.find_one({
                "user_id": current_user["id"],
                "post_id": post["id"]
            })
            is_liked = like is not None
            
            if post.get("is_premium") and post["user_id"] != current_user["id"]:
                is_accessible = await check_subscription(current_user["id"], post["user_id"])
        elif post.get("is_premium"):
            is_accessible = False
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"] if is_accessible else "Premium content",
            media=post.get("media") if is_accessible else None,
            media_type=post.get("media_type", "video"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=is_liked,
            is_accessible=is_accessible
        ))
    
    return result

@api_router.get("/posts/following", response_model=List[Post])
async def get_following_feed(skip: int = 0, limit: int = 20, current_user: dict = Depends(get_current_user)):
    follows = await db.follows.find({"follower_id": current_user["id"]}).to_list(1000)
    following_ids = [f["following_id"] for f in follows]
    following_ids.append(current_user["id"])
    
    posts = await db.posts.find({"user_id": {"$in": following_ids}}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    
    result = []
    for post in posts:
        like = await db.likes.find_one({
            "user_id": current_user["id"],
            "post_id": post["id"]
        })
        
        is_accessible = True
        if post.get("is_premium") and post["user_id"] != current_user["id"]:
            is_accessible = await check_subscription(current_user["id"], post["user_id"])
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"] if is_accessible else "Premium content - Subscribe to view",
            media=post.get("media") if is_accessible else None,
            media_type=post.get("media_type", "image"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=like is not None,
            is_accessible=is_accessible
        ))
    
    return result

@api_router.get("/posts/user/{user_id}", response_model=List[Post])
async def get_user_posts(user_id: str, skip: int = 0, limit: int = 20, current_user: dict = Depends(get_optional_user)):
    posts = await db.posts.find({"user_id": user_id}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    
    result = []
    for post in posts:
        is_liked = False
        is_accessible = True
        
        if current_user:
            like = await db.likes.find_one({
                "user_id": current_user["id"],
                "post_id": post["id"]
            })
            is_liked = like is not None
            
            if post.get("is_premium") and post["user_id"] != current_user["id"]:
                is_accessible = await check_subscription(current_user["id"], post["user_id"])
        elif post.get("is_premium"):
            is_accessible = False
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"] if is_accessible else "Premium content",
            media=post.get("media") if is_accessible else None,
            media_type=post.get("media_type", "image"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=is_liked,
            is_accessible=is_accessible
        ))
    
    return result

@api_router.get("/posts/boosted", response_model=List[Post])
async def get_boosted_posts_feed(limit: int = 5, current_user: dict = Depends(get_optional_user)):
    """Get currently boosted posts for feed insertion"""
    now = datetime.utcnow()
    boosts = await db.boosted_posts.find({
        "status": "active",
        "start_date": {"$lte": now},
        "end_date": {"$gte": now}
    }).limit(limit).to_list(limit)
    
    if not boosts:
        return []
    
    post_ids = [b["post_id"] for b in boosts]
    posts = await db.posts.find({"id": {"$in": post_ids}}).to_list(limit)
    
    result = []
    for post in posts:
        is_liked = False
        if current_user:
            like = await db.likes.find_one({
                "user_id": current_user["id"],
                "post_id": post["id"]
            })
            is_liked = like is not None
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"],
            media=post.get("media"),
            media_type=post.get("media_type", "image"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=is_liked,
            is_accessible=True,
            is_boosted=True
        ))
    
    return result

@api_router.get("/posts/{post_id}", response_model=Post)
async def get_post(post_id: str, current_user: dict = Depends(get_optional_user)):
    post = await db.posts.find_one({"id": post_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    is_liked = False
    is_accessible = True
    
    if current_user:
        like = await db.likes.find_one({
            "user_id": current_user["id"],
            "post_id": post["id"]
        })
        is_liked = like is not None
        
        if post.get("is_premium") and post["user_id"] != current_user["id"]:
            is_accessible = await check_subscription(current_user["id"], post["user_id"])
    elif post.get("is_premium"):
        is_accessible = False
    
    return Post(
        id=post["id"],
        user_id=post["user_id"],
        username=post["username"],
        display_name=post["display_name"],
        user_avatar=post.get("user_avatar"),
        content=post["content"] if is_accessible else "Premium content - Subscribe to view",
        media=post.get("media") if is_accessible else None,
        media_type=post.get("media_type", "image"),
        is_premium=post.get("is_premium", False),
        likes_count=post.get("likes_count", 0),
        comments_count=post.get("comments_count", 0),
        created_at=post["created_at"],
        is_liked=is_liked,
        is_accessible=is_accessible
    )

@api_router.delete("/posts/{post_id}")
async def delete_post(post_id: str, current_user: dict = Depends(get_current_user)):
    post = await db.posts.find_one({"id": post_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if post["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this post")
    
    await db.posts.delete_one({"id": post_id})
    await db.likes.delete_many({"post_id": post_id})
    await db.comments.delete_many({"post_id": post_id})
    await db.users.update_one({"id": current_user["id"]}, {"$inc": {"posts_count": -1}})
    
    return {"message": "Post deleted successfully"}

# ============== LIKE ROUTES ==============

@api_router.post("/posts/{post_id}/like")
async def like_post(post_id: str, current_user: dict = Depends(get_current_user)):
    post = await db.posts.find_one({"id": post_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    existing_like = await db.likes.find_one({
        "user_id": current_user["id"],
        "post_id": post_id
    })
    
    if existing_like:
        raise HTTPException(status_code=400, detail="Already liked this post")
    
    like_doc = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "post_id": post_id,
        "created_at": datetime.utcnow()
    }
    
    await db.likes.insert_one(like_doc)
    await db.posts.update_one({"id": post_id}, {"$inc": {"likes_count": 1}})
    
    # Create notification
    await create_notification(
        user_id=post["user_id"],
        notification_type="like",
        from_user=current_user,
        message="liked your post",
        reference_id=post_id
    )
    
    return {"message": "Post liked"}

@api_router.delete("/posts/{post_id}/like")
async def unlike_post(post_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.likes.delete_one({
        "user_id": current_user["id"],
        "post_id": post_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=400, detail="Not liked this post")
    
    await db.posts.update_one({"id": post_id}, {"$inc": {"likes_count": -1}})
    
    return {"message": "Post unliked"}

# ============== COMMENT ROUTES ==============

@api_router.post("/posts/{post_id}/comments", response_model=Comment)
async def create_comment(post_id: str, comment_data: CommentCreate, current_user: dict = Depends(get_current_user)):
    # Check if user is banned
    ban = await check_user_banned(current_user["id"])
    if ban:
        raise HTTPException(
            status_code=403, 
            detail=f"Your account is banned until {ban['ban_expires_at'].strftime('%Y-%m-%d')}. Reason: {ban['reason']}"
        )
    
    post = await db.posts.find_one({"id": post_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # AI Moderation check
    moderation_result = await check_content_moderation(comment_data.content, current_user["id"], "comment")
    if not moderation_result["is_appropriate"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Your comment was flagged for inappropriate content: {moderation_result.get('reason', 'Community guidelines violation')}. Please be respectful to creators."
        )
    
    comment_id = str(uuid.uuid4())
    comment_doc = {
        "id": comment_id,
        "post_id": post_id,
        "user_id": current_user["id"],
        "username": current_user["username"],
        "display_name": current_user["display_name"],
        "user_avatar": current_user.get("avatar"),
        "content": comment_data.content,
        "created_at": datetime.utcnow()
    }
    
    await db.comments.insert_one(comment_doc)
    await db.posts.update_one({"id": post_id}, {"$inc": {"comments_count": 1}})
    
    # Create notification
    await create_notification(
        user_id=post["user_id"],
        notification_type="comment",
        from_user=current_user,
        message=f"commented: \"{comment_data.content[:50]}...\"" if len(comment_data.content) > 50 else f"commented: \"{comment_data.content}\"",
        reference_id=post_id
    )
    
    return Comment(**comment_doc)

@api_router.get("/posts/{post_id}/comments", response_model=List[Comment])
async def get_comments(post_id: str, skip: int = 0, limit: int = 50):
    comments = await db.comments.find({"post_id": post_id}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return [Comment(**c) for c in comments]

@api_router.delete("/posts/{post_id}/comments/{comment_id}")
async def delete_comment(post_id: str, comment_id: str, current_user: dict = Depends(get_current_user)):
    comment = await db.comments.find_one({"id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    if comment["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment")
    
    await db.comments.delete_one({"id": comment_id})
    await db.posts.update_one({"id": post_id}, {"$inc": {"comments_count": -1}})
    
    return {"message": "Comment deleted"}

# ============== STORY ROUTES ==============

@api_router.post("/stories", response_model=Story)
async def create_story(story_data: StoryCreate, current_user: dict = Depends(get_current_user)):
    story_id = str(uuid.uuid4())
    now = datetime.utcnow()
    expires_at = now + timedelta(hours=24)
    
    story_doc = {
        "id": story_id,
        "user_id": current_user["id"],
        "username": current_user["username"],
        "display_name": current_user["display_name"],
        "user_avatar": current_user.get("avatar"),
        "media": story_data.media,
        "media_type": story_data.media_type,
        "created_at": now,
        "expires_at": expires_at,
        "views_count": 0,
        "viewed_by": []
    }
    
    await db.stories.insert_one(story_doc)
    
    return Story(**{k: v for k, v in story_doc.items() if k != "viewed_by"})

@api_router.get("/stories", response_model=List[StoryGroup])
async def get_stories(current_user: dict = Depends(get_current_user)):
    follows = await db.follows.find({"follower_id": current_user["id"]}).to_list(1000)
    following_ids = [f["following_id"] for f in follows]
    following_ids.append(current_user["id"])
    
    now = datetime.utcnow()
    
    stories = await db.stories.find({
        "user_id": {"$in": following_ids},
        "expires_at": {"$gt": now}
    }).sort("created_at", -1).to_list(1000)
    
    user_stories = {}
    for story in stories:
        user_id = story["user_id"]
        if user_id not in user_stories:
            user_stories[user_id] = {
                "user_id": user_id,
                "username": story["username"],
                "display_name": story["display_name"],
                "user_avatar": story.get("user_avatar"),
                "stories": [],
                "has_unseen": False
            }
        
        has_seen = current_user["id"] in story.get("viewed_by", [])
        if not has_seen:
            user_stories[user_id]["has_unseen"] = True
        
        user_stories[user_id]["stories"].append(Story(
            id=story["id"],
            user_id=story["user_id"],
            username=story["username"],
            display_name=story["display_name"],
            user_avatar=story.get("user_avatar"),
            media=story["media"],
            media_type=story["media_type"],
            created_at=story["created_at"],
            expires_at=story["expires_at"],
            views_count=story.get("views_count", 0)
        ))
    
    result = list(user_stories.values())
    result.sort(key=lambda x: (not x["has_unseen"], -x["stories"][0].created_at.timestamp() if x["stories"] else 0))
    
    return [StoryGroup(**sg) for sg in result]

@api_router.post("/stories/{story_id}/view")
async def view_story(story_id: str, current_user: dict = Depends(get_current_user)):
    story = await db.stories.find_one({"id": story_id})
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    
    if current_user["id"] not in story.get("viewed_by", []):
        await db.stories.update_one(
            {"id": story_id},
            {
                "$addToSet": {"viewed_by": current_user["id"]},
                "$inc": {"views_count": 1}
            }
        )
    
    return {"message": "Story viewed"}

@api_router.delete("/stories/{story_id}")
async def delete_story(story_id: str, current_user: dict = Depends(get_current_user)):
    story = await db.stories.find_one({"id": story_id})
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    
    if story["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this story")
    
    await db.stories.delete_one({"id": story_id})
    
    return {"message": "Story deleted"}

# ============== NOTIFICATION ROUTES ==============

@api_router.get("/notifications", response_model=List[Notification])
async def get_notifications(skip: int = 0, limit: int = 50, current_user: dict = Depends(get_current_user)):
    notifications = await db.notifications.find({"user_id": current_user["id"]}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return [Notification(**n) for n in notifications]

@api_router.get("/notifications/unread-count")
async def get_unread_count(current_user: dict = Depends(get_current_user)):
    count = await db.notifications.count_documents({"user_id": current_user["id"], "is_read": False})
    return {"count": count}

@api_router.put("/notifications/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    await db.notifications.update_many(
        {"user_id": current_user["id"], "is_read": False},
        {"$set": {"is_read": True}}
    )
    return {"message": "All notifications marked as read"}

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.notifications.update_one(
        {"id": notification_id, "user_id": current_user["id"]},
        {"$set": {"is_read": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}

# ============== MESSAGING ROUTES ==============

@api_router.get("/conversations", response_model=List[ConversationPreview])
async def get_conversations(current_user: dict = Depends(get_current_user)):
    conversations = await db.conversations.find({
        "participants": current_user["id"]
    }).sort("updated_at", -1).to_list(100)
    
    result = []
    for conv in conversations:
        other_user_id = [p for p in conv["participants"] if p != current_user["id"]][0]
        other_user = await db.users.find_one({"id": other_user_id})
        
        if other_user:
            unread = await db.messages.count_documents({
                "conversation_id": conv["id"],
                "sender_id": {"$ne": current_user["id"]},
                "is_read": False
            })
            
            result.append(ConversationPreview(
                id=conv["id"],
                participant_id=other_user["id"],
                participant_username=other_user["username"],
                participant_display_name=other_user["display_name"],
                participant_avatar=other_user.get("avatar"),
                last_message=conv.get("last_message"),
                last_message_time=conv.get("updated_at"),
                unread_count=unread
            ))
    
    return result

@api_router.post("/conversations/{user_id}", response_model=ConversationPreview)
async def create_or_get_conversation(user_id: str, current_user: dict = Depends(get_current_user)):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot message yourself")
    
    other_user = await db.users.find_one({"id": user_id})
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    existing = await db.conversations.find_one({
        "participants": {"$all": [current_user["id"], user_id]}
    })
    
    if existing:
        return ConversationPreview(
            id=existing["id"],
            participant_id=other_user["id"],
            participant_username=other_user["username"],
            participant_display_name=other_user["display_name"],
            participant_avatar=other_user.get("avatar"),
            last_message=existing.get("last_message"),
            last_message_time=existing.get("updated_at"),
            unread_count=0
        )
    
    conv_id = str(uuid.uuid4())
    conv_doc = {
        "id": conv_id,
        "participants": [current_user["id"], user_id],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "last_message": None
    }
    
    await db.conversations.insert_one(conv_doc)
    
    return ConversationPreview(
        id=conv_id,
        participant_id=other_user["id"],
        participant_username=other_user["username"],
        participant_display_name=other_user["display_name"],
        participant_avatar=other_user.get("avatar"),
        last_message=None,
        last_message_time=None,
        unread_count=0
    )

@api_router.get("/conversations/{conversation_id}/messages", response_model=List[Message])
async def get_messages(conversation_id: str, skip: int = 0, limit: int = 50, current_user: dict = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conversation_id})
    if not conv or current_user["id"] not in conv["participants"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    messages = await db.messages.find({"conversation_id": conversation_id}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    
    # Mark messages as read
    await db.messages.update_many(
        {"conversation_id": conversation_id, "sender_id": {"$ne": current_user["id"]}, "is_read": False},
        {"$set": {"is_read": True}}
    )
    
    return [Message(**m) for m in reversed(messages)]

@api_router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(conversation_id: str, message_data: MessageCreate, current_user: dict = Depends(get_current_user)):
    # Check if user is banned
    ban = await check_user_banned(current_user["id"])
    if ban:
        raise HTTPException(
            status_code=403, 
            detail=f"Your account is banned until {ban['ban_expires_at'].strftime('%Y-%m-%d')}. Reason: {ban['reason']}"
        )
    
    conv = await db.conversations.find_one({"id": conversation_id})
    if not conv or current_user["id"] not in conv["participants"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # AI Moderation check for DMs
    moderation_result = await check_content_moderation(message_data.content, current_user["id"], "message")
    if not moderation_result["is_appropriate"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Your message was flagged for inappropriate content: {moderation_result.get('reason', 'Community guidelines violation')}. Please be respectful."
        )
    
    message_id = str(uuid.uuid4())
    message_doc = {
        "id": message_id,
        "conversation_id": conversation_id,
        "sender_id": current_user["id"],
        "sender_username": current_user["username"],
        "sender_display_name": current_user["display_name"],
        "sender_avatar": current_user.get("avatar"),
        "content": message_data.content,
        "media": message_data.media,
        "media_type": message_data.media_type,
        "created_at": datetime.utcnow(),
        "is_read": False
    }
    
    await db.messages.insert_one(message_doc)
    
    # Update conversation
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": {"last_message": message_data.content[:100], "updated_at": datetime.utcnow()}}
    )
    
    # Send notification to other user
    other_user_id = [p for p in conv["participants"] if p != current_user["id"]][0]
    await create_notification(
        user_id=other_user_id,
        notification_type="message",
        from_user=current_user,
        message=f"sent you a message: \"{message_data.content[:30]}...\"" if len(message_data.content) > 30 else f"sent you a message: \"{message_data.content}\"",
        reference_id=conversation_id
    )
    
    return Message(**message_doc)

# ============== SUBSCRIPTION & PAYMENT ROUTES (MOCKED) ==============

@api_router.get("/subscriptions", response_model=List[Subscription])
async def get_my_subscriptions(current_user: dict = Depends(get_current_user)):
    subs = await db.subscriptions.find({
        "subscriber_id": current_user["id"],
        "status": "active"
    }).to_list(100)
    
    result = []
    for sub in subs:
        creator = await db.users.find_one({"id": sub["creator_id"]})
        if creator:
            result.append(Subscription(
                id=sub["id"],
                subscriber_id=sub["subscriber_id"],
                creator_id=sub["creator_id"],
                creator_username=creator["username"],
                creator_display_name=creator["display_name"],
                creator_avatar=creator.get("avatar"),
                price=sub["price"],
                status=sub["status"],
                created_at=sub["created_at"],
                expires_at=sub["expires_at"]
            ))
    
    return result

@api_router.get("/users/{user_id}/subscription-status")
async def get_subscription_status(user_id: str, current_user: dict = Depends(get_current_user)):
    is_subscribed = await check_subscription(current_user["id"], user_id)
    creator = await db.users.find_one({"id": user_id})
    return {
        "is_subscribed": is_subscribed,
        "price": creator.get("subscription_price", 9.99) if creator else 9.99
    }

@api_router.post("/users/{creator_id}/subscribe")
async def subscribe_to_creator(creator_id: str, current_user: dict = Depends(get_current_user)):
    """Mock subscription - requires ID verification first"""
    
    # Check if user is banned
    ban = await check_user_banned(current_user["id"])
    if ban:
        raise HTTPException(
            status_code=403, 
            detail=f"Your account is banned until {ban['ban_expires_at'].strftime('%Y-%m-%d')}. Reason: {ban['reason']}"
        )
    
    # Check ID verification
    is_verified = await check_id_verified(current_user["id"])
    if not is_verified:
        raise HTTPException(
            status_code=403, 
            detail="ID verification required to subscribe to creators. Please verify your ID first."
        )
    
    if creator_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot subscribe to yourself")
    
    creator = await db.users.find_one({"id": creator_id})
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    
    if not creator.get("is_premium_creator"):
        raise HTTPException(status_code=400, detail="User is not a premium creator")
    
    existing = await db.subscriptions.find_one({
        "subscriber_id": current_user["id"],
        "creator_id": creator_id,
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Already subscribed")
    
    # Create mock payment
    payment_id = str(uuid.uuid4())
    payment_doc = {
        "id": payment_id,
        "user_id": current_user["id"],
        "amount": creator.get("subscription_price", 9.99),
        "currency": "usd",
        "status": "succeeded",  # Mock: always succeeds
        "type": "subscription",
        "creator_id": creator_id,
        "created_at": datetime.utcnow()
    }
    await db.payments.insert_one(payment_doc)
    
    # Create subscription
    sub_id = str(uuid.uuid4())
    now = datetime.utcnow()
    sub_doc = {
        "id": sub_id,
        "subscriber_id": current_user["id"],
        "creator_id": creator_id,
        "price": creator.get("subscription_price", 9.99),
        "status": "active",
        "created_at": now,
        "expires_at": now + timedelta(days=30)
    }
    await db.subscriptions.insert_one(sub_doc)
    
    # Notify creator
    await create_notification(
        user_id=creator_id,
        notification_type="subscription",
        from_user=current_user,
        message=f"subscribed to your content for ${creator.get('subscription_price', 9.99)}/month"
    )
    
    return {
        "message": "Successfully subscribed! (MOCKED PAYMENT)",
        "subscription_id": sub_id,
        "expires_at": sub_doc["expires_at"].isoformat()
    }

@api_router.delete("/users/{creator_id}/subscribe")
async def cancel_subscription(creator_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.subscriptions.update_one(
        {
            "subscriber_id": current_user["id"],
            "creator_id": creator_id,
            "status": "active"
        },
        {"$set": {"status": "cancelled"}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=400, detail="No active subscription found")
    
    return {"message": "Subscription cancelled"}

@api_router.post("/users/{user_id}/tip", response_model=Tip)
async def send_tip(user_id: str, tip_data: TipCreate, current_user: dict = Depends(get_current_user)):
    """Mock tip - simulates payment and records tip"""
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot tip yourself")
    
    if tip_data.amount < 1:
        raise HTTPException(status_code=400, detail="Minimum tip is $1")
    
    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create mock payment
    payment_id = str(uuid.uuid4())
    payment_doc = {
        "id": payment_id,
        "user_id": current_user["id"],
        "amount": tip_data.amount,
        "currency": "usd",
        "status": "succeeded",  # Mock: always succeeds
        "type": "tip",
        "recipient_id": user_id,
        "created_at": datetime.utcnow()
    }
    await db.payments.insert_one(payment_doc)
    
    # Record tip
    tip_id = str(uuid.uuid4())
    tip_doc = {
        "id": tip_id,
        "from_user_id": current_user["id"],
        "from_username": current_user["username"],
        "to_user_id": user_id,
        "to_username": target_user["username"],
        "amount": tip_data.amount,
        "message": tip_data.message or "",
        "created_at": datetime.utcnow()
    }
    await db.tips.insert_one(tip_doc)
    
    # Update creator balance (mock)
    await db.users.update_one({"id": user_id}, {"$inc": {"balance": tip_data.amount}})
    
    # Notify creator
    await create_notification(
        user_id=user_id,
        notification_type="tip",
        from_user=current_user,
        message=f"sent you a ${tip_data.amount:.2f} tip!" + (f" \"{tip_data.message}\"" if tip_data.message else "")
    )
    
    return Tip(**tip_doc)

@api_router.get("/earnings")
async def get_earnings(current_user: dict = Depends(get_current_user)):
    """Get creator earnings summary (mocked)"""
    tips_total = 0
    tips = await db.tips.find({"to_user_id": current_user["id"]}).to_list(1000)
    tips_total = sum(t["amount"] for t in tips)
    
    subs_count = await db.subscriptions.count_documents({
        "creator_id": current_user["id"],
        "status": "active"
    })
    
    sub_price = current_user.get("subscription_price", 9.99)
    
    return {
        "tips_total": tips_total,
        "subscriptions_count": subs_count,
        "subscription_revenue": subs_count * sub_price,
        "total_earnings": tips_total + (subs_count * sub_price),
        "balance": current_user.get("balance", 0),
        "note": "MOCKED - No real payments processed"
    }

# ============== EXPLORE/SEARCH ROUTES ==============

@api_router.get("/explore/users", response_model=List[UserProfile])
async def search_users(q: str = "", skip: int = 0, limit: int = 20):
    query = {}
    if q:
        query = {
            "$or": [
                {"username": {"$regex": q, "$options": "i"}},
                {"display_name": {"$regex": q, "$options": "i"}}
            ]
        }
    
    users = await db.users.find(query).skip(skip).limit(limit).to_list(limit)
    return [UserProfile(
        id=u["id"],
        email=u["email"],
        username=u["username"],
        display_name=u["display_name"],
        bio=u.get("bio", ""),
        avatar=u.get("avatar"),
        followers_count=u.get("followers_count", 0),
        following_count=u.get("following_count", 0),
        posts_count=u.get("posts_count", 0),
        is_premium_creator=u.get("is_premium_creator", False),
        subscription_price=u.get("subscription_price", 9.99),
        created_at=u["created_at"]
    ) for u in users]

@api_router.get("/explore/trending", response_model=List[Post])
async def get_trending_posts(skip: int = 0, limit: int = 20, current_user: dict = Depends(get_optional_user)):
    posts = await db.posts.find().sort("likes_count", -1).skip(skip).limit(limit).to_list(limit)
    
    result = []
    for post in posts:
        is_liked = False
        is_accessible = True
        
        if current_user:
            like = await db.likes.find_one({
                "user_id": current_user["id"],
                "post_id": post["id"]
            })
            is_liked = like is not None
            
            if post.get("is_premium") and post["user_id"] != current_user["id"]:
                is_accessible = await check_subscription(current_user["id"], post["user_id"])
        elif post.get("is_premium"):
            is_accessible = False
        
        result.append(Post(
            id=post["id"],
            user_id=post["user_id"],
            username=post["username"],
            display_name=post["display_name"],
            user_avatar=post.get("user_avatar"),
            content=post["content"] if is_accessible else "Premium content",
            media=post.get("media") if is_accessible else None,
            media_type=post.get("media_type", "image"),
            is_premium=post.get("is_premium", False),
            likes_count=post.get("likes_count", 0),
            comments_count=post.get("comments_count", 0),
            created_at=post["created_at"],
            is_liked=is_liked,
            is_accessible=is_accessible
        ))
    
    return result

@api_router.get("/explore/creators", response_model=List[UserProfile])
async def get_premium_creators(skip: int = 0, limit: int = 20):
    """Get list of premium creators"""
    users = await db.users.find({"is_premium_creator": True}).sort("followers_count", -1).skip(skip).limit(limit).to_list(limit)
    return [UserProfile(
        id=u["id"],
        email=u["email"],
        username=u["username"],
        display_name=u["display_name"],
        bio=u.get("bio", ""),
        avatar=u.get("avatar"),
        followers_count=u.get("followers_count", 0),
        following_count=u.get("following_count", 0),
        posts_count=u.get("posts_count", 0),
        is_premium_creator=u.get("is_premium_creator", False),
        subscription_price=u.get("subscription_price", 9.99),
        created_at=u["created_at"]
    ) for u in users]

# ============== ID VERIFICATION ROUTES ==============

@api_router.get("/verification/status")
async def get_verification_status(current_user: dict = Depends(get_current_user)):
    """Check user's ID verification status"""
    verification = await db.id_verifications.find_one({"user_id": current_user["id"]})
    
    if not verification:
        return {"verified": False, "status": "not_submitted", "message": "ID verification required to subscribe to creators"}
    
    if verification["status"] == "approved":
        return {"verified": True, "status": "approved", "message": "Your ID has been verified"}
    elif verification["status"] == "pending":
        return {"verified": False, "status": "pending", "message": "Your ID verification is being reviewed"}
    else:
        return {"verified": False, "status": "rejected", "message": verification.get("rejection_reason", "Verification rejected")}

@api_router.post("/verification/submit")
async def submit_id_verification(data: IDVerificationSubmit, current_user: dict = Depends(get_current_user)):
    """Submit ID for verification (MOCKED - auto-approves after submission)"""
    
    # Check if already verified
    existing = await db.id_verifications.find_one({"user_id": current_user["id"], "status": "approved"})
    if existing:
        return {"success": True, "message": "Already verified", "status": "approved"}
    
    # Check for pending verification
    pending = await db.id_verifications.find_one({"user_id": current_user["id"], "status": "pending"})
    if pending:
        # MOCKED: Auto-approve pending verifications
        await db.id_verifications.update_one(
            {"id": pending["id"]},
            {"$set": {"status": "approved", "reviewed_at": datetime.utcnow()}}
        )
        return {"success": True, "message": "Verification approved", "status": "approved"}
    
    # Create new verification
    verification_id = str(uuid.uuid4())
    verification = {
        "id": verification_id,
        "user_id": current_user["id"],
        "id_image": data.id_image[:100] + "...",  # Store truncated for demo (don't store full images in production)
        "status": "approved",  # MOCKED: Auto-approve
        "submitted_at": datetime.utcnow(),
        "reviewed_at": datetime.utcnow()  # MOCKED: Instant review
    }
    
    await db.id_verifications.insert_one(verification)
    
    return {
        "success": True,
        "message": "ID verified successfully (MOCKED)",
        "status": "approved",
        "note": "In production, this would require manual review or integration with ID verification service"
    }

# ============== USER REPORTS & MODERATION ROUTES ==============

@api_router.post("/reports")
async def report_user(data: ReportCreate, current_user: dict = Depends(get_current_user)):
    """Report a user for inappropriate behavior"""
    
    # Check if reported user exists
    reported_user = await db.users.find_one({"id": data.reported_user_id})
    if not reported_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Can't report yourself
    if data.reported_user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot report yourself")
    
    report = {
        "id": str(uuid.uuid4()),
        "reporter_id": current_user["id"],
        "reporter_username": current_user["username"],
        "reported_user_id": data.reported_user_id,
        "reported_username": reported_user["username"],
        "reason": data.reason,
        "content_type": data.content_type,
        "content_id": data.content_id,
        "content_text": data.content_text,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    
    await db.reports.insert_one(report)
    
    # Check if user has multiple reports - auto-ban if 3+ reports
    report_count = await db.reports.count_documents({
        "reported_user_id": data.reported_user_id,
        "status": {"$in": ["pending", "action_taken"]}
    })
    
    if report_count >= 3:
        # Auto-ban for 1 month
        await ban_user(data.reported_user_id, "Multiple user reports for inappropriate behavior", "system")
        await db.reports.update_many(
            {"reported_user_id": data.reported_user_id, "status": "pending"},
            {"$set": {"status": "action_taken", "action_taken": "User banned for 30 days"}}
        )
    
    return {"success": True, "message": "Report submitted successfully"}

@api_router.get("/reports/my-reports")
async def get_my_reports(current_user: dict = Depends(get_current_user)):
    """Get reports submitted by the current user"""
    reports = await db.reports.find({"reporter_id": current_user["id"]}).sort("created_at", -1).to_list(50)
    return reports

@api_router.get("/ban/status")
async def get_ban_status(current_user: dict = Depends(get_current_user)):
    """Check if user is currently banned"""
    ban = await check_user_banned(current_user["id"])
    
    if ban:
        days_remaining = (ban["ban_expires_at"] - datetime.utcnow()).days
        return {
            "is_banned": True,
            "reason": ban["reason"],
            "banned_at": ban["banned_at"].isoformat(),
            "expires_at": ban["ban_expires_at"].isoformat(),
            "days_remaining": max(0, days_remaining)
        }
    
    return {"is_banned": False}

# ============== MODERATION MIDDLEWARE ==============

async def check_content_moderation(content: str, user_id: str, content_type: str = "comment") -> dict:
    """Check content for inappropriate material and take action if needed"""
    moderation_result = await moderate_content_ai(content)
    
    if not moderation_result["is_appropriate"]:
        # Log the violation
        violation = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "content_type": content_type,
            "content": content,
            "reason": moderation_result.get("reason"),
            "flagged_content": moderation_result.get("flagged_content"),
            "confidence": moderation_result.get("confidence", 0),
            "created_at": datetime.utcnow()
        }
        await db.content_violations.insert_one(violation)
        
        # Check violation count for this user
        violation_count = await db.content_violations.count_documents({
            "user_id": user_id,
            "created_at": {"$gt": datetime.utcnow() - timedelta(days=30)}
        })
        
        # Auto-ban after 3 violations in 30 days
        if violation_count >= 3:
            existing_ban = await check_user_banned(user_id)
            if not existing_ban:
                await ban_user(user_id, "Repeated violations of community guidelines", "system")
    
    return moderation_result

# ============== STRIPE PAYMENT ROUTES ==============

@api_router.post("/payments/create-checkout")
async def create_checkout_session(data: CheckoutRequest, current_user: dict = Depends(get_current_user)):
    """Create Stripe checkout session for subscription or tip"""
    
    # Check if user is banned
    ban = await check_user_banned(current_user["id"])
    if ban:
        raise HTTPException(status_code=403, detail="Your account is banned")
    
    # Check ID verification for subscriptions
    if data.payment_type == "subscription":
        is_verified = await check_id_verified(current_user["id"])
        if not is_verified:
            raise HTTPException(status_code=403, detail="ID verification required")
    
    # Get creator info
    creator = await db.users.find_one({"id": data.creator_id})
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    
    if not creator.get("is_premium_creator"):
        raise HTTPException(status_code=400, detail="User is not a premium creator")
    
    # Calculate amounts
    if data.payment_type == "subscription":
        amount = creator.get("subscription_price", 9.99)
    else:
        if not data.tip_amount or data.tip_amount < 1:
            raise HTTPException(status_code=400, detail="Minimum tip is $1")
        amount = data.tip_amount
    
    amount_cents = int(amount * 100)  # Stripe uses cents
    platform_fee_cents = int(amount_cents * (PLATFORM_FEE_PERCENT / 100))
    
    try:
        # Create Stripe Checkout Session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"{'Subscription to' if data.payment_type == 'subscription' else 'Tip for'} @{creator['username']}",
                        'description': f"Support {creator['display_name']} on AllTogether",
                    },
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/payment-cancel",
            metadata={
                'user_id': current_user["id"],
                'creator_id': data.creator_id,
                'payment_type': data.payment_type,
                'platform_fee_cents': str(platform_fee_cents),
            }
        )
        
        # Store transaction in database
        platform_fee = round(amount * (PLATFORM_FEE_PERCENT / 100), 2)
        creator_amount = round(amount - platform_fee, 2)
        
        transaction = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "creator_id": data.creator_id,
            "session_id": checkout_session.id,
            "payment_type": data.payment_type,
            "amount": amount,
            "platform_fee": platform_fee,
            "creator_amount": creator_amount,
            "currency": "usd",
            "status": "pending",
            "metadata": {
                "creator_username": creator["username"],
                "user_username": current_user["username"]
            },
            "created_at": datetime.utcnow()
        }
        await db.payment_transactions.insert_one(transaction)
        
        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
            "amount": amount,
            "platform_fee": platform_fee,
            "creator_amount": creator_amount
        }
    except stripe.error.StripeError as e:
        logging.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")
    except Exception as e:
        logging.error(f"Checkout error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")

@api_router.get("/payments/verify/{session_id}")
async def verify_payment(session_id: str, current_user: dict = Depends(get_current_user)):
    """Verify payment status and activate subscription/process tip"""
    
    transaction = await db.payment_transactions.find_one({
        "session_id": session_id,
        "user_id": current_user["id"]
    })
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction["status"] == "paid":
        return {"status": "paid", "message": "Payment already processed"}
    
    try:
        # Check payment status with Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        if checkout_session.payment_status == "paid":
            # Update transaction
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {"status": "paid", "updated_at": datetime.utcnow()}}
            )
            
            # Process based on payment type
            if transaction["payment_type"] == "subscription":
                # Create subscription
                sub_id = str(uuid.uuid4())
                now = datetime.utcnow()
                sub_doc = {
                    "id": sub_id,
                    "subscriber_id": current_user["id"],
                    "creator_id": transaction["creator_id"],
                    "price": transaction["amount"],
                    "status": "active",
                    "created_at": now,
                    "expires_at": now + timedelta(days=30)
                }
                await db.subscriptions.insert_one(sub_doc)
                
                # Update creator earnings
                await db.users.update_one(
                    {"id": transaction["creator_id"]},
                    {"$inc": {"total_earnings": transaction["creator_amount"], "pending_payout": transaction["creator_amount"]}}
                )
            else:
                # Process tip
                tip_doc = {
                    "id": str(uuid.uuid4()),
                    "from_user_id": current_user["id"],
                    "to_user_id": transaction["creator_id"],
                    "amount": transaction["amount"],
                    "platform_fee": transaction["platform_fee"],
                    "creator_amount": transaction["creator_amount"],
                    "created_at": datetime.utcnow()
                }
                await db.tips.insert_one(tip_doc)
                
                # Update creator earnings
                await db.users.update_one(
                    {"id": transaction["creator_id"]},
                    {"$inc": {"total_earnings": transaction["creator_amount"], "tips_received": transaction["creator_amount"], "pending_payout": transaction["creator_amount"]}}
                )
            
            # Update platform earnings
            await db.platform_stats.update_one(
                {"id": "main"},
                {
                    "$inc": {
                        "total_revenue": transaction["amount"],
                        "platform_earnings": transaction["platform_fee"],
                        "creator_payouts": transaction["creator_amount"],
                        "total_transactions": 1
                    },
                    "$setOnInsert": {"id": "main", "created_at": datetime.utcnow()}
                },
                upsert=True
            )
            
            # Create notification
            await create_notification(
                user_id=transaction["creator_id"],
                notification_type="subscription" if transaction["payment_type"] == "subscription" else "tip",
                from_user=current_user,
                message=f"{'subscribed to you' if transaction['payment_type'] == 'subscription' else 'sent you a tip'} - ${transaction['amount']:.2f} (you receive ${transaction['creator_amount']:.2f})"
            )
            
            return {
                "status": "paid",
                "message": f"{'Subscription activated!' if transaction['payment_type'] == 'subscription' else 'Tip sent!'} Thank you!",
                "creator_earnings": transaction["creator_amount"],
                "platform_fee": transaction["platform_fee"]
            }
        elif checkout_session.status == "expired":
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {"status": "expired", "updated_at": datetime.utcnow()}}
            )
            return {"status": "expired", "message": "Payment session expired"}
        else:
            return {"status": "pending", "message": "Payment not completed yet"}
    except stripe.error.StripeError as e:
        logging.error(f"Stripe verification error: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify payment")

# ============== STRIPE CONNECT - CREATOR PAYOUTS ==============

@api_router.post("/connect/onboard")
async def create_connect_account(current_user: dict = Depends(get_current_user)):
    """Create Stripe Connect account for creator payouts"""
    
    if not current_user.get("is_premium_creator"):
        raise HTTPException(status_code=400, detail="Only premium creators can set up payouts")
    
    # Check if user already has a Connect account
    if current_user.get("stripe_account_id"):
        # Return onboarding link if not completed
        account = stripe.Account.retrieve(current_user["stripe_account_id"])
        if account.details_submitted:
            return {"status": "complete", "message": "Stripe account already set up"}
    
    try:
        # Create Express Connect account
        account = stripe.Account.create(
            type="express",
            country="US",
            email=current_user["email"],
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            business_type="individual",
            metadata={
                "user_id": current_user["id"],
                "username": current_user["username"]
            }
        )
        
        # Save account ID to user
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"stripe_account_id": account.id}}
        )
        
        # Create account link for onboarding
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=f"https://viralx-preview.preview.emergentagent.com/connect-refresh",
            return_url=f"https://viralx-preview.preview.emergentagent.com/connect-success",
            type="account_onboarding",
        )
        
        return {
            "status": "onboarding",
            "onboarding_url": account_link.url,
            "account_id": account.id
        }
    except stripe.error.StripeError as e:
        logging.error(f"Stripe Connect error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create payout account: {str(e)}")

@api_router.get("/connect/status")
async def get_connect_status(current_user: dict = Depends(get_current_user)):
    """Check creator's Stripe Connect account status"""
    
    if not current_user.get("stripe_account_id"):
        return {
            "status": "not_setup",
            "message": "Payout account not set up",
            "can_receive_payouts": False
        }
    
    try:
        account = stripe.Account.retrieve(current_user["stripe_account_id"])
        
        return {
            "status": "active" if account.details_submitted else "incomplete",
            "details_submitted": account.details_submitted,
            "payouts_enabled": account.payouts_enabled,
            "can_receive_payouts": account.payouts_enabled,
            "pending_payout": current_user.get("pending_payout", 0)
        }
    except stripe.error.StripeError as e:
        logging.error(f"Connect status error: {e}")
        return {"status": "error", "message": str(e)}

@api_router.post("/connect/payout")
async def request_payout(current_user: dict = Depends(get_current_user)):
    """Request payout of pending earnings"""
    
    if not current_user.get("stripe_account_id"):
        raise HTTPException(status_code=400, detail="Payout account not set up")
    
    pending_amount = current_user.get("pending_payout", 0)
    if pending_amount < 10:
        raise HTTPException(status_code=400, detail="Minimum payout is $10")
    
    try:
        # Check if account can receive payouts
        account = stripe.Account.retrieve(current_user["stripe_account_id"])
        if not account.payouts_enabled:
            raise HTTPException(status_code=400, detail="Payout account setup incomplete")
        
        # Create transfer to connected account
        transfer = stripe.Transfer.create(
            amount=int(pending_amount * 100),  # In cents
            currency="usd",
            destination=current_user["stripe_account_id"],
            metadata={
                "user_id": current_user["id"],
                "username": current_user["username"]
            }
        )
        
        # Record payout
        payout_doc = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "stripe_transfer_id": transfer.id,
            "amount": pending_amount,
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.payouts.insert_one(payout_doc)
        
        # Reset pending payout
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"pending_payout": 0}, "$inc": {"total_payouts": pending_amount}}
        )
        
        return {
            "success": True,
            "amount": pending_amount,
            "transfer_id": transfer.id,
            "message": f"${pending_amount:.2f} has been sent to your bank account"
        }
    except stripe.error.StripeError as e:
        logging.error(f"Payout error: {e}")
        raise HTTPException(status_code=500, detail=f"Payout failed: {str(e)}")

@api_router.get("/connect/payouts")
async def get_payout_history(current_user: dict = Depends(get_current_user)):
    """Get creator's payout history"""
    
    payouts = await db.payouts.find({"user_id": current_user["id"]}).sort("created_at", -1).limit(50).to_list(50)
    
    return [{
        "id": p["id"],
        "amount": p["amount"],
        "status": p["status"],
        "created_at": p["created_at"].isoformat()
    } for p in payouts]

# ============== ADMIN DASHBOARD ==============

@api_router.get("/admin/dashboard")
async def get_admin_dashboard(current_user: dict = Depends(get_current_user)):
    """Get admin dashboard with platform statistics"""
    
    # Get platform stats
    stats = await db.platform_stats.find_one({"id": "main"})
    
    if not stats:
        stats = {
            "total_revenue": 0,
            "platform_earnings": 0,
            "creator_payouts": 0,
            "total_transactions": 0
        }
    
    # Get user counts
    total_users = await db.users.count_documents({})
    total_creators = await db.users.count_documents({"is_premium_creator": True})
    
    # Get recent transactions
    recent_transactions = await db.payment_transactions.find({"status": "paid"}).sort("created_at", -1).limit(10).to_list(10)
    
    # Get ad revenue
    ad_revenue_pipeline = [
        {"$group": {
            "_id": None,
            "total": {"$sum": "$spent"}
        }}
    ]
    ad_result = await db.ads.aggregate(ad_revenue_pipeline).to_list(1)
    ad_revenue = ad_result[0]["total"] if ad_result else 0
    
    # Get today's stats
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_transactions = await db.payment_transactions.count_documents({
        "status": "paid",
        "created_at": {"$gte": today_start}
    })
    
    today_revenue_pipeline = [
        {"$match": {"status": "paid", "created_at": {"$gte": today_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$platform_fee"}}}
    ]
    today_result = await db.payment_transactions.aggregate(today_revenue_pipeline).to_list(1)
    today_revenue = today_result[0]["total"] if today_result else 0
    
    return {
        "overview": {
            "total_revenue": round(stats.get("total_revenue", 0), 2),
            "platform_earnings": round(stats.get("platform_earnings", 0), 2),
            "creator_payouts": round(stats.get("creator_payouts", 0), 2),
            "total_transactions": stats.get("total_transactions", 0),
            "ad_revenue": round(ad_revenue, 2),
            "total_income": round(stats.get("platform_earnings", 0) + ad_revenue, 2)
        },
        "today": {
            "transactions": today_transactions,
            "revenue": round(today_revenue, 2)
        },
        "users": {
            "total": total_users,
            "creators": total_creators,
            "subscribers": total_users - total_creators
        },
        "recent_transactions": [{
            "id": t["id"],
            "type": t["payment_type"],
            "amount": t["amount"],
            "platform_fee": t["platform_fee"],
            "creator_amount": t["creator_amount"],
            "created_at": t["created_at"].isoformat()
        } for t in recent_transactions],
        "platform_fee_percent": PLATFORM_FEE_PERCENT
    }

@api_router.get("/admin/creators")
async def get_creator_stats(current_user: dict = Depends(get_current_user)):
    """Get all creators with their earnings"""
    
    creators = await db.users.find({"is_premium_creator": True}).to_list(100)
    
    creator_stats = []
    for creator in creators:
        sub_count = await db.subscriptions.count_documents({
            "creator_id": creator["id"],
            "status": "active"
        })
        
        creator_stats.append({
            "id": creator["id"],
            "username": creator["username"],
            "display_name": creator["display_name"],
            "avatar": creator.get("avatar"),
            "subscription_price": creator.get("subscription_price", 9.99),
            "total_earnings": round(creator.get("total_earnings", 0), 2),
            "pending_payout": round(creator.get("pending_payout", 0), 2),
            "subscribers": sub_count,
            "has_stripe_account": bool(creator.get("stripe_account_id"))
        })
    
    return creator_stats

@api_router.get("/admin/revenue-chart")
async def get_revenue_chart(days: int = 30, current_user: dict = Depends(get_current_user)):
    """Get daily revenue data for charts"""
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    pipeline = [
        {"$match": {"status": "paid", "created_at": {"$gte": start_date}}},
        {"$group": {
            "_id": {
                "year": {"$year": "$created_at"},
                "month": {"$month": "$created_at"},
                "day": {"$dayOfMonth": "$created_at"}
            },
            "revenue": {"$sum": "$platform_fee"},
            "transactions": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    
    results = await db.payment_transactions.aggregate(pipeline).to_list(days)
    
    return [{
        "date": f"{r['_id']['year']}-{r['_id']['month']:02d}-{r['_id']['day']:02d}",
        "revenue": round(r["revenue"], 2),
        "transactions": r["transactions"]
    } for r in results]

# ============== ADVERTISING ROUTES ==============

@api_router.post("/ads/create")
async def create_ad(data: AdCreate, current_user: dict = Depends(get_current_user)):
    """Create a new advertisement"""
    
    if data.budget < 10:
        raise HTTPException(status_code=400, detail="Minimum ad budget is $10")
    
    ad_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    ad_doc = {
        "id": ad_id,
        "advertiser_id": current_user["id"],
        "advertiser_name": current_user["display_name"],
        "ad_type": data.ad_type,
        "title": data.title,
        "content": data.content,
        "media": data.media,
        "link_url": data.link_url,
        "cta_text": data.cta_text,
        "impressions": 0,
        "clicks": 0,
        "budget": data.budget,
        "spent": 0.0,
        "cpm_rate": 5.0,  # $5 per 1000 impressions
        "cpc_rate": 0.50,  # $0.50 per click
        "is_active": True,
        "start_date": now,
        "end_date": now + timedelta(days=data.days_to_run),
        "created_at": now
    }
    
    await db.ads.insert_one(ad_doc)
    
    return {
        "success": True,
        "ad_id": ad_id,
        "message": "Ad created and is now live!",
        "estimated_impressions": int(data.budget / 5.0 * 1000)  # Based on CPM
    }

@api_router.get("/ads/feed")
async def get_feed_ads(limit: int = 3):
    """Get sponsored posts for the feed"""
    
    now = datetime.utcnow()
    ads = await db.ads.find({
        "ad_type": "sponsored_post",
        "is_active": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
        "$expr": {"$lt": ["$spent", "$budget"]}
    }).limit(limit).to_list(limit)
    
    # Format ads as sponsored posts
    sponsored_posts = []
    for ad in ads:
        sponsored_posts.append({
            "id": ad["id"],
            "is_ad": True,
            "ad_type": "sponsored_post",
            "advertiser_name": ad["advertiser_name"],
            "title": ad["title"],
            "content": ad["content"],
            "media": ad.get("media"),
            "link_url": ad["link_url"],
            "cta_text": ad["cta_text"],
            "sponsored_label": "Sponsored"
        })
    
    return sponsored_posts

@api_router.get("/ads/banner")
async def get_banner_ad():
    """Get a banner ad for display"""
    
    now = datetime.utcnow()
    ad = await db.ads.find_one({
        "ad_type": "banner",
        "is_active": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
        "$expr": {"$lt": ["$spent", "$budget"]}
    })
    
    if not ad:
        return None
    
    return {
        "id": ad["id"],
        "is_ad": True,
        "ad_type": "banner",
        "title": ad["title"],
        "content": ad["content"],
        "media": ad.get("media"),
        "link_url": ad["link_url"],
        "cta_text": ad["cta_text"]
    }

@api_router.post("/ads/{ad_id}/impression")
async def record_impression(ad_id: str, current_user: dict = Depends(get_current_user)):
    """Record an ad impression"""
    
    ad = await db.ads.find_one({"id": ad_id})
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    
    # Calculate cost
    cost = ad["cpm_rate"] / 1000  # Cost per impression
    
    # Update ad stats
    await db.ads.update_one(
        {"id": ad_id},
        {"$inc": {"impressions": 1, "spent": cost}}
    )
    
    # Record impression
    impression = {
        "id": str(uuid.uuid4()),
        "ad_id": ad_id,
        "user_id": current_user["id"],
        "created_at": datetime.utcnow()
    }
    await db.ad_impressions.insert_one(impression)
    
    # Check if budget exhausted
    updated_ad = await db.ads.find_one({"id": ad_id})
    if updated_ad["spent"] >= updated_ad["budget"]:
        await db.ads.update_one({"id": ad_id}, {"$set": {"is_active": False}})
    
    return {"success": True}

@api_router.post("/ads/{ad_id}/click")
async def record_click(ad_id: str, current_user: dict = Depends(get_current_user)):
    """Record an ad click"""
    
    ad = await db.ads.find_one({"id": ad_id})
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    
    # Calculate cost
    cost = ad["cpc_rate"]
    
    # Update ad stats
    await db.ads.update_one(
        {"id": ad_id},
        {"$inc": {"clicks": 1, "spent": cost}}
    )
    
    # Record click
    click = {
        "id": str(uuid.uuid4()),
        "ad_id": ad_id,
        "user_id": current_user["id"],
        "created_at": datetime.utcnow()
    }
    await db.ad_clicks.insert_one(click)
    
    return {"success": True, "link_url": ad["link_url"]}

@api_router.get("/ads/my-ads")
async def get_my_ads(current_user: dict = Depends(get_current_user)):
    """Get ads created by current user"""
    
    ads = await db.ads.find({"advertiser_id": current_user["id"]}).sort("created_at", -1).to_list(50)
    
    return [{
        "id": ad["id"],
        "ad_type": ad["ad_type"],
        "title": ad["title"],
        "budget": ad["budget"],
        "spent": round(ad["spent"], 2),
        "impressions": ad["impressions"],
        "clicks": ad["clicks"],
        "ctr": round((ad["clicks"] / ad["impressions"] * 100) if ad["impressions"] > 0 else 0, 2),
        "is_active": ad["is_active"],
        "start_date": ad["start_date"].isoformat(),
        "end_date": ad["end_date"].isoformat()
    } for ad in ads]

@api_router.get("/ads/revenue")
async def get_ad_revenue():
    """Get total ad revenue for the platform"""
    
    pipeline = [
        {"$group": {
            "_id": None,
            "total_ad_revenue": {"$sum": "$spent"},
            "total_impressions": {"$sum": "$impressions"},
            "total_clicks": {"$sum": "$clicks"},
            "total_ads": {"$sum": 1}
        }}
    ]
    
    result = await db.ads.aggregate(pipeline).to_list(1)
    
    if result:
        stats = result[0]
        return {
            "total_ad_revenue": round(stats.get("total_ad_revenue", 0), 2),
            "total_impressions": stats.get("total_impressions", 0),
            "total_clicks": stats.get("total_clicks", 0),
            "total_ads": stats.get("total_ads", 0),
            "avg_ctr": round((stats.get("total_clicks", 0) / stats.get("total_impressions", 1) * 100), 2)
        }
    
    return {"total_ad_revenue": 0, "total_impressions": 0, "total_clicks": 0, "total_ads": 0, "avg_ctr": 0}

# ============== HEALTH CHECK ==============

# Define gift types and coin packages (constants)
VIRTUAL_GIFTS = [
    {"id": "rose", "name": "Rose", "icon": "🌹", "coin_cost": 10, "dollar_value": 0.10, "creator_earnings": 0.07},
    {"id": "heart", "name": "Heart", "icon": "❤️", "coin_cost": 50, "dollar_value": 0.50, "creator_earnings": 0.35},
    {"id": "star", "name": "Star", "icon": "⭐", "coin_cost": 100, "dollar_value": 1.00, "creator_earnings": 0.70},
    {"id": "diamond", "name": "Diamond", "icon": "💎", "coin_cost": 500, "dollar_value": 5.00, "creator_earnings": 3.50},
    {"id": "crown", "name": "Crown", "icon": "👑", "coin_cost": 1000, "dollar_value": 10.00, "creator_earnings": 7.00},
    {"id": "rocket", "name": "Rocket", "icon": "🚀", "coin_cost": 5000, "dollar_value": 50.00, "creator_earnings": 35.00},
]

COIN_PACKAGES = [
    {"id": "starter", "coins": 100, "price": 0.99, "bonus_coins": 0},
    {"id": "popular", "coins": 500, "price": 4.99, "bonus_coins": 50},
    {"id": "value", "coins": 1000, "price": 9.99, "bonus_coins": 150},
    {"id": "super", "coins": 5000, "price": 39.99, "bonus_coins": 1000},
    {"id": "mega", "coins": 10000, "price": 69.99, "bonus_coins": 3000},
]

ANALYTICS_PLANS = {
    "basic": {"price": 9.99, "name": "Basic Analytics"},
    "pro": {"price": 19.99, "name": "Pro Analytics"},
    "enterprise": {"price": 49.99, "name": "Enterprise Analytics"},
}

FEATURED_SPOT_PRICES = {
    "explore_top": 29.99,
    "explore_creators": 19.99,
    "suggested": 14.99,
}

# ============== 1. VERIFIED BADGES ROUTES ==============

@api_router.get("/verified/status")
async def get_verified_status(current_user: dict = Depends(get_current_user)):
    """Check user's verified badge status"""
    badge = await db.verified_badges.find_one({
        "user_id": current_user["id"],
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    
    if badge:
        return {
            "is_verified": True,
            "expires_at": badge["expires_at"].isoformat(),
            "auto_renew": badge.get("auto_renew", True)
        }
    return {"is_verified": False}

@api_router.post("/verified/purchase")
async def purchase_verified_badge(data: VerifiedBadgeRequest, current_user: dict = Depends(get_current_user)):
    """Purchase verified badge ($4.99/month)"""
    
    # Check if already verified
    existing = await db.verified_badges.find_one({
        "user_id": current_user["id"],
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    if existing:
        raise HTTPException(status_code=400, detail="You already have a verified badge")
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Verified Badge',
                        'description': 'Blue checkmark badge for 1 month',
                    },
                    'unit_amount': 499,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/verified-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/verified-cancel",
            metadata={
                'user_id': current_user["id"],
                'purchase_type': 'verified_badge',
            }
        )
        
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/verified/activate/{session_id}")
async def activate_verified_badge(session_id: str, current_user: dict = Depends(get_current_user)):
    """Activate verified badge after payment"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        now = datetime.utcnow()
        badge = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "status": "active",
            "price": 4.99,
            "started_at": now,
            "expires_at": now + timedelta(days=30),
            "auto_renew": True
        }
        await db.verified_badges.insert_one(badge)
        await db.users.update_one({"id": current_user["id"]}, {"$set": {"is_verified": True}})
        
        # Track platform revenue
        await db.platform_stats.update_one(
            {"id": "main"},
            {"$inc": {"verified_badge_revenue": 4.99}},
            upsert=True
        )
        
        return {"success": True, "message": "Verified badge activated!", "expires_at": badge["expires_at"].isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============== 2. BOOSTED POSTS ROUTES ==============

@api_router.post("/boost/post")
async def boost_post(data: BoostPostRequest, current_user: dict = Depends(get_current_user)):
    """Boost a post to reach more people"""
    
    if data.budget < 5:
        raise HTTPException(status_code=400, detail="Minimum boost budget is $5")
    
    post = await db.posts.find_one({"id": data.post_id, "user_id": current_user["id"]})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found or not yours")
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'Boost Post',
                        'description': f'Promote your post for {data.days} days',
                    },
                    'unit_amount': int(data.budget * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/boost-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/boost-cancel",
            metadata={
                'user_id': current_user["id"],
                'post_id': data.post_id,
                'purchase_type': 'boost_post',
                'budget': str(data.budget),
                'days': str(data.days),
            }
        )
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/boost/activate/{session_id}")
async def activate_boost(session_id: str, current_user: dict = Depends(get_current_user)):
    """Activate post boost after payment"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        metadata = checkout_session.metadata
        now = datetime.utcnow()
        budget = float(metadata.get('budget', 5))
        days = int(metadata.get('days', 7))
        
        boost = {
            "id": str(uuid.uuid4()),
            "post_id": metadata['post_id'],
            "user_id": current_user["id"],
            "budget": budget,
            "spent": 0.0,
            "impressions": 0,
            "clicks": 0,
            "cpm_rate": 3.0,
            "status": "active",
            "start_date": now,
            "end_date": now + timedelta(days=days),
            "target_impressions": int(budget / 3.0 * 1000)
        }
        await db.boosted_posts.insert_one(boost)
        await db.posts.update_one({"id": metadata['post_id']}, {"$set": {"is_boosted": True}})
        
        await db.platform_stats.update_one({"id": "main"}, {"$inc": {"boost_revenue": budget}}, upsert=True)
        
        return {"success": True, "message": "Post boosted!", "estimated_reach": boost["target_impressions"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/boost/my-boosts")
async def get_my_boosts(current_user: dict = Depends(get_current_user)):
    """Get user's boosted posts"""
    boosts = await db.boosted_posts.find({"user_id": current_user["id"]}).sort("start_date", -1).to_list(50)
    return boosts

@api_router.get("/posts/boosted")
async def get_boosted_posts(limit: int = 5):
    """Get currently boosted posts for feed insertion"""
    now = datetime.utcnow()
    boosts = await db.boosted_posts.find({
        "status": "active",
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
        "$expr": {"$lt": ["$spent", "$budget"]}
    }).limit(limit).to_list(limit)
    
    post_ids = [b["post_id"] for b in boosts]
    posts = await db.posts.find({"id": {"$in": post_ids}}).to_list(limit)
    return posts

# ============== 3. VIRTUAL GIFTS/COINS ROUTES ==============

@api_router.get("/coins/packages")
async def get_coin_packages():
    """Get available coin packages"""
    return COIN_PACKAGES

@api_router.get("/coins/gifts")
async def get_virtual_gifts():
    """Get available virtual gifts"""
    return VIRTUAL_GIFTS

@api_router.get("/coins/balance")
async def get_coin_balance(current_user: dict = Depends(get_current_user)):
    """Get user's coin balance"""
    user_coins = await db.user_coins.find_one({"user_id": current_user["id"]})
    if not user_coins:
        return {"balance": 0, "total_purchased": 0, "total_spent": 0}
    return {
        "balance": user_coins.get("balance", 0),
        "total_purchased": user_coins.get("total_purchased", 0),
        "total_spent": user_coins.get("total_spent", 0)
    }

@api_router.post("/coins/buy")
async def buy_coins(data: BuyCoinsRequest, current_user: dict = Depends(get_current_user)):
    """Purchase coins"""
    package = next((p for p in COIN_PACKAGES if p["id"] == data.package_id), None)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{package["coins"]} Coins',
                        'description': f'+ {package["bonus_coins"]} bonus coins' if package["bonus_coins"] > 0 else 'Virtual coins for gifts',
                    },
                    'unit_amount': int(package["price"] * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/coins-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/coins-cancel",
            metadata={
                'user_id': current_user["id"],
                'purchase_type': 'coins',
                'package_id': data.package_id,
                'coins': str(package["coins"]),
                'bonus': str(package["bonus_coins"]),
            }
        )
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/coins/activate/{session_id}")
async def activate_coins(session_id: str, current_user: dict = Depends(get_current_user)):
    """Add coins after payment"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        metadata = checkout_session.metadata
        coins = int(metadata.get('coins', 0))
        bonus = int(metadata.get('bonus', 0))
        total_coins = coins + bonus
        
        await db.user_coins.update_one(
            {"user_id": current_user["id"]},
            {
                "$inc": {"balance": total_coins, "total_purchased": total_coins},
                "$setOnInsert": {"user_id": current_user["id"]}
            },
            upsert=True
        )
        
        package = next((p for p in COIN_PACKAGES if p["id"] == metadata.get('package_id')), None)
        if package:
            await db.platform_stats.update_one({"id": "main"}, {"$inc": {"coins_revenue": package["price"]}}, upsert=True)
        
        return {"success": True, "coins_added": total_coins, "message": f"Added {total_coins} coins!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/gifts/send")
async def send_gift(data: SendGiftRequest, current_user: dict = Depends(get_current_user)):
    """Send a virtual gift to a user"""
    gift = next((g for g in VIRTUAL_GIFTS if g["id"] == data.gift_id), None)
    if not gift:
        raise HTTPException(status_code=404, detail="Gift not found")
    
    # Check coin balance
    user_coins = await db.user_coins.find_one({"user_id": current_user["id"]})
    balance = user_coins.get("balance", 0) if user_coins else 0
    
    if balance < gift["coin_cost"]:
        raise HTTPException(status_code=400, detail=f"Not enough coins. Need {gift['coin_cost']}, have {balance}")
    
    # Deduct coins
    await db.user_coins.update_one(
        {"user_id": current_user["id"]},
        {"$inc": {"balance": -gift["coin_cost"], "total_spent": gift["coin_cost"]}}
    )
    
    # Add earnings to recipient
    await db.users.update_one(
        {"id": data.recipient_id},
        {"$inc": {"gift_earnings": gift["creator_earnings"], "pending_payout": gift["creator_earnings"]}}
    )
    
    # Record gift
    gift_record = {
        "id": str(uuid.uuid4()),
        "from_user_id": current_user["id"],
        "to_user_id": data.recipient_id,
        "gift_id": data.gift_id,
        "gift_name": gift["name"],
        "gift_icon": gift["icon"],
        "coin_cost": gift["coin_cost"],
        "dollar_value": gift["dollar_value"],
        "creator_earnings": gift["creator_earnings"],
        "post_id": data.post_id,
        "message": data.message,
        "created_at": datetime.utcnow()
    }
    await db.gift_history.insert_one(gift_record)
    
    # Notification
    await create_notification(
        user_id=data.recipient_id,
        notification_type="gift",
        from_user=current_user,
        message=f"sent you a {gift['icon']} {gift['name']}!"
    )
    
    return {"success": True, "message": f"Sent {gift['icon']} {gift['name']}!"}

@api_router.get("/gifts/received")
async def get_received_gifts(current_user: dict = Depends(get_current_user)):
    """Get gifts received by user"""
    gifts = await db.gift_history.find({"to_user_id": current_user["id"]}).sort("created_at", -1).limit(100).to_list(100)
    return gifts

# ============== 4. FEATURED SPOTS ROUTES ==============

@api_router.get("/featured/prices")
async def get_featured_prices():
    """Get featured spot prices"""
    return FEATURED_SPOT_PRICES

@api_router.post("/featured/purchase")
async def purchase_featured_spot(data: FeaturedSpotRequest, current_user: dict = Depends(get_current_user)):
    """Purchase a featured spot"""
    if data.spot_type not in FEATURED_SPOT_PRICES:
        raise HTTPException(status_code=400, detail="Invalid spot type")
    
    price = FEATURED_SPOT_PRICES[data.spot_type] * data.days / 7  # Price per week
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'Featured Spot: {data.spot_type.replace("_", " ").title()}',
                        'description': f'Featured placement for {data.days} days',
                    },
                    'unit_amount': int(price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/featured-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/featured-cancel",
            metadata={
                'user_id': current_user["id"],
                'purchase_type': 'featured_spot',
                'spot_type': data.spot_type,
                'days': str(data.days),
                'price': str(price),
            }
        )
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/featured/activate/{session_id}")
async def activate_featured_spot(session_id: str, current_user: dict = Depends(get_current_user)):
    """Activate featured spot after payment"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        metadata = checkout_session.metadata
        now = datetime.utcnow()
        
        spot = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "spot_type": metadata['spot_type'],
            "price": float(metadata.get('price', 0)),
            "start_date": now,
            "end_date": now + timedelta(days=int(metadata.get('days', 7))),
            "impressions": 0,
            "clicks": 0,
            "status": "active"
        }
        await db.featured_spots.insert_one(spot)
        
        await db.platform_stats.update_one({"id": "main"}, {"$inc": {"featured_revenue": spot["price"]}}, upsert=True)
        
        return {"success": True, "message": "Featured spot activated!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/featured/users/{spot_type}")
async def get_featured_users(spot_type: str, limit: int = 10):
    """Get users with active featured spots"""
    now = datetime.utcnow()
    spots = await db.featured_spots.find({
        "spot_type": spot_type,
        "status": "active",
        "start_date": {"$lte": now},
        "end_date": {"$gte": now}
    }).limit(limit).to_list(limit)
    
    user_ids = [s["user_id"] for s in spots]
    users = await db.users.find({"id": {"$in": user_ids}}).to_list(limit)
    return users

# ============== 5. PREMIUM ANALYTICS ROUTES ==============

@api_router.get("/analytics/plans")
async def get_analytics_plans():
    """Get analytics subscription plans"""
    return ANALYTICS_PLANS

@api_router.get("/analytics/status")
async def get_analytics_status(current_user: dict = Depends(get_current_user)):
    """Check user's analytics subscription"""
    sub = await db.analytics_subscriptions.find_one({
        "user_id": current_user["id"],
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    if sub:
        return {"has_analytics": True, "plan": sub["plan"], "expires_at": sub["expires_at"].isoformat()}
    return {"has_analytics": False}

@api_router.post("/analytics/subscribe")
async def subscribe_analytics(data: AnalyticsRequest, current_user: dict = Depends(get_current_user)):
    """Subscribe to premium analytics"""
    if data.plan not in ANALYTICS_PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    
    plan = ANALYTICS_PLANS[data.plan]
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': plan["name"],
                        'description': 'Premium analytics for 1 month',
                    },
                    'unit_amount': int(plan["price"] * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/analytics-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/analytics-cancel",
            metadata={
                'user_id': current_user["id"],
                'purchase_type': 'analytics',
                'plan': data.plan,
            }
        )
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/analytics/activate/{session_id}")
async def activate_analytics(session_id: str, current_user: dict = Depends(get_current_user)):
    """Activate analytics subscription"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        metadata = checkout_session.metadata
        plan = ANALYTICS_PLANS.get(metadata['plan'], ANALYTICS_PLANS['basic'])
        now = datetime.utcnow()
        
        sub = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "plan": metadata['plan'],
            "price": plan["price"],
            "status": "active",
            "started_at": now,
            "expires_at": now + timedelta(days=30)
        }
        await db.analytics_subscriptions.insert_one(sub)
        
        await db.platform_stats.update_one({"id": "main"}, {"$inc": {"analytics_revenue": plan["price"]}}, upsert=True)
        
        return {"success": True, "message": f"{plan['name']} activated!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/analytics/data")
async def get_analytics_data(current_user: dict = Depends(get_current_user)):
    """Get detailed analytics (requires subscription)"""
    # Check subscription
    sub = await db.analytics_subscriptions.find_one({
        "user_id": current_user["id"],
        "status": "active",
        "expires_at": {"$gt": datetime.utcnow()}
    })
    if not sub:
        raise HTTPException(status_code=403, detail="Premium analytics subscription required")
    
    # Get analytics data
    user_id = current_user["id"]
    
    # Post performance
    posts = await db.posts.find({"user_id": user_id}).to_list(100)
    total_likes = sum(p.get("likes_count", 0) for p in posts)
    total_comments = sum(p.get("comments_count", 0) for p in posts)
    
    # Follower growth (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # Earnings breakdown
    earnings = {
        "subscriptions": current_user.get("total_earnings", 0) - current_user.get("tips_received", 0) - current_user.get("gift_earnings", 0),
        "tips": current_user.get("tips_received", 0),
        "gifts": current_user.get("gift_earnings", 0),
        "total": current_user.get("total_earnings", 0)
    }
    
    return {
        "overview": {
            "total_posts": len(posts),
            "total_likes": total_likes,
            "total_comments": total_comments,
            "followers": current_user.get("followers_count", 0),
            "following": current_user.get("following_count", 0)
        },
        "earnings": earnings,
        "engagement_rate": round((total_likes + total_comments) / max(len(posts), 1) / max(current_user.get("followers_count", 1), 1) * 100, 2),
        "top_posts": sorted(posts, key=lambda x: x.get("likes_count", 0), reverse=True)[:5],
        "plan": sub["plan"]
    }

# ============== 6. PROMOTED PROFILES ROUTES ==============

@api_router.post("/promote/profile")
async def promote_profile(data: PromoteProfileRequest, current_user: dict = Depends(get_current_user)):
    """Promote your profile to appear in suggestions"""
    if data.budget < 10:
        raise HTTPException(status_code=400, detail="Minimum promotion budget is $10")
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Profile Promotion',
                        'description': f'Promote your profile for {data.duration_days} days',
                    },
                    'unit_amount': int(data.budget * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{data.origin_url}/promote-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{data.origin_url}/promote-cancel",
            metadata={
                'user_id': current_user["id"],
                'purchase_type': 'profile_promotion',
                'budget': str(data.budget),
                'days': str(data.duration_days),
            }
        )
        return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/promote/activate/{session_id}")
async def activate_profile_promotion(session_id: str, current_user: dict = Depends(get_current_user)):
    """Activate profile promotion"""
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        metadata = checkout_session.metadata
        now = datetime.utcnow()
        budget = float(metadata.get('budget', 10))
        
        promo = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "budget": budget,
            "spent": 0.0,
            "impressions": 0,
            "profile_visits": 0,
            "new_followers": 0,
            "cpm_rate": 4.0,
            "status": "active",
            "start_date": now,
            "end_date": now + timedelta(days=int(metadata.get('days', 7)))
        }
        await db.promoted_profiles.insert_one(promo)
        
        await db.platform_stats.update_one({"id": "main"}, {"$inc": {"promotion_revenue": budget}}, upsert=True)
        
        return {"success": True, "message": "Profile promotion activated!", "estimated_reach": int(budget / 4 * 1000)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/promote/suggested-users")
async def get_suggested_promoted_users(limit: int = 10):
    """Get promoted users for suggestions"""
    now = datetime.utcnow()
    promos = await db.promoted_profiles.find({
        "status": "active",
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
        "$expr": {"$lt": ["$spent", "$budget"]}
    }).limit(limit).to_list(limit)
    
    user_ids = [p["user_id"] for p in promos]
    users = await db.users.find({"id": {"$in": user_ids}}).to_list(limit)
    return users

@api_router.get("/promote/my-promotions")
async def get_my_promotions(current_user: dict = Depends(get_current_user)):
    """Get user's profile promotions"""
    promos = await db.promoted_profiles.find({"user_id": current_user["id"]}).sort("start_date", -1).to_list(50)
    return promos

# ============== UPDATED ADMIN DASHBOARD ==============

@api_router.get("/admin/full-revenue")
async def get_full_revenue_breakdown(current_user: dict = Depends(get_current_user)):
    """Get complete revenue breakdown from all sources"""
    stats = await db.platform_stats.find_one({"id": "main"})
    if not stats:
        stats = {}
    
    return {
        "subscriptions_and_tips": {
            "total": round(stats.get("platform_earnings", 0), 2),
            "description": "15% fee from subscriptions and tips"
        },
        "verified_badges": {
            "total": round(stats.get("verified_badge_revenue", 0), 2),
            "price": 4.99,
            "description": "$4.99/month verified badges"
        },
        "boosted_posts": {
            "total": round(stats.get("boost_revenue", 0), 2),
            "description": "Post promotion purchases"
        },
        "virtual_coins": {
            "total": round(stats.get("coins_revenue", 0), 2),
            "description": "Coin package purchases"
        },
        "featured_spots": {
            "total": round(stats.get("featured_revenue", 0), 2),
            "description": "Featured placement purchases"
        },
        "premium_analytics": {
            "total": round(stats.get("analytics_revenue", 0), 2),
            "description": "Analytics subscriptions"
        },
        "profile_promotions": {
            "total": round(stats.get("promotion_revenue", 0), 2),
            "description": "Profile promotion purchases"
        },
        "advertising": {
            "total": round(stats.get("ad_revenue", 0), 2),
            "description": "Ad impressions and clicks"
        },
        "grand_total": round(
            stats.get("platform_earnings", 0) +
            stats.get("verified_badge_revenue", 0) +
            stats.get("boost_revenue", 0) +
            stats.get("coins_revenue", 0) +
            stats.get("featured_revenue", 0) +
            stats.get("analytics_revenue", 0) +
            stats.get("promotion_revenue", 0) +
            stats.get("ad_revenue", 0),
            2
        )
    }

# ============== ORIGINAL HEALTH CHECK ==============

@api_router.get("/")
async def root():
    return {"message": "AllTogether API is running", "version": "2.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "database": "connected"}

@api_router.get("/earnings/summary")
async def get_earnings_summary(current_user: dict = Depends(get_current_user)):
    """Get creator earnings summary"""
    user_id = current_user["id"]
    
    # Get total earnings from transactions
    pipeline = [
        {"$match": {"creator_id": user_id, "status": "completed"}},
        {"$group": {"_id": None, "total": {"$sum": "$creator_amount"}}}
    ]
    total_result = await db.transactions.aggregate(pipeline).to_list(1)
    total_earnings = total_result[0]["total"] if total_result else 0
    
    # Get monthly earnings
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    monthly_pipeline = [
        {"$match": {"creator_id": user_id, "status": "completed", "created_at": {"$gte": month_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$creator_amount"}}}
    ]
    monthly_result = await db.transactions.aggregate(monthly_pipeline).to_list(1)
    monthly_earnings = monthly_result[0]["total"] if monthly_result else 0
    
    # Get pending payout (earnings not yet paid out)
    pending_pipeline = [
        {"$match": {"creator_id": user_id, "status": "completed", "paid_out": {"$ne": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$creator_amount"}}}
    ]
    pending_result = await db.transactions.aggregate(pending_pipeline).to_list(1)
    pending_payout = pending_result[0]["total"] if pending_result else 0
    
    # Get total views and likes on user's posts
    user_posts = await db.posts.find({"user_id": user_id}).to_list(1000)
    total_views = sum(p.get("views_count", 0) for p in user_posts)
    total_likes = sum(p.get("likes_count", 0) for p in user_posts)
    
    return {
        "total_earnings": total_earnings,
        "monthly_earnings": monthly_earnings,
        "pending_payout": pending_payout,
        "total_views": total_views,
        "total_likes": total_likes
    }

# Static pages routes (Privacy Policy, Terms of Service) - served via /api prefix
@api_router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy():
    """Serve privacy policy page"""
    file_path = STATIC_DIR / "privacy-policy.html"
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Privacy policy not found")

@api_router.get("/terms-of-service", response_class=HTMLResponse)
async def terms_of_service():
    """Serve terms of service page"""
    file_path = STATIC_DIR / "terms-of-service.html"
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Terms of service not found")

@api_router.get("/terms", response_class=HTMLResponse)
async def terms_redirect():
    """Alias for terms of service"""
    return await terms_of_service()

@api_router.get("/privacy", response_class=HTMLResponse)
async def privacy_redirect():
    """Alias for privacy policy"""
    return await privacy_policy()

@api_router.get("/viralx-download")
async def download_viralx():
    """Download ViralX app code"""
    file_path = STATIC_DIR / "viralx-app.zip"
    if file_path.exists():
        return FileResponse(file_path, filename="viralx-app.zip", media_type="application/zip")
    raise HTTPException(status_code=404, detail="Download not found")

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
