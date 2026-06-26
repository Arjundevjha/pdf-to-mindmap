-- SQL Migration: Remove obsolete custom users table and constraints
-- Run this in your Supabase Dashboard SQL Editor (https://supabase.com/dashboard/project/_/sql)

-- 1. Drop the foreign key constraint from the documents table
ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS documents_user_email_fkey;

-- 2. Drop the users table
DROP TABLE IF EXISTS public.users;
